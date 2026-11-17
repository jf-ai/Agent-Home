"""
Tests for agent CRUD operations (agent/crud.py)
"""
import hashlib
import json
import uuid

import pytest
import pytest_asyncio
from pydantic_ai.tools import ToolDefinition
from sqlalchemy.ext.asyncio import AsyncSession

from agent.crud import (
    ContextStatus,
    create_agent_record,
    get_agent_record,
    get_agent_record_by_name,
    get_context_status,
    replace_agent_config,
    replace_system_instructions,
    tool_defs_wire_bytes,
)
from agent.deps import AgentDeps
from conftest import (
    DBTestBase,
    SAMPLE_AGENT_CONFIG,
    commit_and_detach_fixture_record,
    make_message_record,
)
from db.connection import ReadOnlySession
from db.models import AgentRecord, SystemPromptSnapshot, ToolDefinitionSnapshot
from messages.messages import _ensure_tool_definition_snapshotted, load_messages


# --- create_agent_record tests ---

class TestCreateAgentRecord(DBTestBase):
    """Tests share the same setup: one created record per test."""

    @pytest_asyncio.fixture(autouse=True)
    async def created_record(self, session: AsyncSession):
        self.record = await create_agent_record(
            session, "test-agent", "You are helpful.", SAMPLE_AGENT_CONFIG
        )
        # create_agent_record flushes only; publish it so the read path (its own
        # connection) can see the row, exactly as prod does before anything queries it.
        self.record = await commit_and_detach_fixture_record(session, self.record)

    async def assert_record_as_expected(self, record_to_check):
        assert uuid.UUID(record_to_check.id)  # raises ValueError if not a valid UUID
        assert record_to_check.name == "test-agent"
        assert record_to_check.system_instructions == "You are helpful."
        assert record_to_check.agent_config == SAMPLE_AGENT_CONFIG
        assert record_to_check.context_window_start == 0
        assert await load_messages(self.ro_session, self.record.id) == []

    async def test_returns_agent_record_with_correct_fields(self):
        """Returned record has expected values — UUID valid, fields match inputs, no history, expected config"""
        await self.assert_record_as_expected(self.record)

    async def test_config_and_data_survive_db_round_trip(self):
        """Record is actually flushed to DB and config deserializes correctly on re-fetch."""
        record_id = self.record.id
        # Read through the read path: no identity map to hit, so this is a real DB read.
        fetched = await get_agent_record(self.ro_session, record_id)
        assert fetched is not None
        await self.assert_record_as_expected(fetched)


# --- get_agent_record / get_agent_record_by_name tests ---
#
# Both functions return AgentRecord | None based on a lookup key.
# Parametrized to test both via the same test logic.

_GET_AGENT_FUNCTIONS = [
    pytest.param(
        get_agent_record,
        lambda r: r.id,
        "nonexistent-id",
        id="by_id",
    ),
    pytest.param(
        get_agent_record_by_name,
        lambda r: r.name,
        "nonexistent-name",
        id="by_name",
    ),
]


@pytest.mark.parametrize("get_fn,key_extractor,unknown_key", _GET_AGENT_FUNCTIONS)
class TestGetAgentRecordFunctions(DBTestBase):
    """Tests for get_agent_record and get_agent_record_by_name."""

    async def test_returns_record_for_known_key(self, agent_record, get_fn, key_extractor, unknown_key):
        result = await get_fn(self.ro_session, key_extractor(agent_record))
        assert isinstance(result, AgentRecord)
        assert result.id == agent_record.id
        assert result.name == agent_record.name
        assert result.agent_config == agent_record.agent_config
        assert result.system_instructions == agent_record.system_instructions

    async def test_returns_none_for_unknown_key(self, get_fn, key_extractor, unknown_key):
        result = await get_fn(self.ro_session, unknown_key)
        assert result is None


# --- replace function tests ---
#
# Common behaviors (commits, round-trip) are tested via parametrization.
# Function-specific behaviors have their own test classes.
#
# Not-found guarantee: replace_* functions take AgentDeps, which proves the agent exists.
# AgentNotFoundError is raised by AgentFactory.build_deps before deps are ever constructed —
# tested in tests/agent/test_factory.py, not here.


# Parametrization data for common replace-function behaviors
# param args: "replace_fn,new_value,attr_name"
_REPLACE_FUNCTIONS = [
    pytest.param(
        replace_agent_config,
        SAMPLE_AGENT_CONFIG.model_copy(update={"soft_compaction_limit": 7777}),
        "agent_config",
        id="replace_agent_config",
    ),
    pytest.param(
        replace_system_instructions,
        "New instructions for test.",
        "system_instructions",
        id="replace_system_instructions",
    ),
]

@pytest.mark.parametrize("replace_fn,new_value,attr_name", _REPLACE_FUNCTIONS)
class TestReplaceFunctionCommonBehaviors:
    """Common behaviors shared by all replace_* functions."""

    async def test_updates_db_and_returns_new_value(
        self, ro_session: ReadOnlySession, agent_record, agent_deps: AgentDeps, replace_fn, new_value, attr_name
    ):
        """Replace functions update DB and return the new value."""
        agent_id = agent_record.id  # capture before expire — post-expire attr access triggers async lazy load
        result = await replace_fn(agent_deps, new_value)

        assert result == new_value
        # agent_record is detached (conftest expunges) — get_agent_record fetches fresh
        refreshed = await get_agent_record(ro_session, agent_id)
        assert refreshed is not None
        assert getattr(refreshed, attr_name) == new_value


class TestReplaceAgentConfig:
    """Function-specific tests for replace_agent_config."""

    @pytest.mark.parametrize("limit_delta,reset_expected", [
        (1,  True),   # limit raised  → fresh warning needed for new uncovered zone
        (0,  False),  # limit unchanged → no new uncovered zone
        (-1, False),  # limit lowered  → compaction fires sooner, consolidation already covers it
    ])
    async def test_compaction_warning_reset_depends_on_limit_direction(
        self,
        agent_record,
        agent_deps: AgentDeps,
        limit_delta: int,
        reset_expected: bool,
    ):
        """compaction_warning_fired is reset only when soft_compaction_limit is raised."""
        async with agent_deps.write_scope():
            agent_deps.compaction_warning_fired = True
        new_limit = SAMPLE_AGENT_CONFIG.soft_compaction_limit + limit_delta
        new_config = SAMPLE_AGENT_CONFIG.model_copy(update={"soft_compaction_limit": new_limit})

        await replace_agent_config(agent_deps, new_config)

        assert agent_deps.compaction_warning_fired is not reset_expected


class TestReplaceSystemInstructions:
    """Function-specific tests for replace_system_instructions."""

    async def test_triggers_recompilation(self, ro_session: ReadOnlySession, agent_record, agent_deps: AgentDeps):
        """System prompt is recompiled after updating instructions."""
        agent_id = agent_record.id  # capture before expire — post-expire attr access triggers async lazy load
        new_instructions = "You are a helpful assistant who loves cats."

        await replace_system_instructions(agent_deps, new_instructions)

        # agent_record is detached (conftest expunges) — get_agent_record fetches fresh
        refreshed = await get_agent_record(ro_session, agent_id)
        assert refreshed is not None
        assert refreshed.compiled_system_prompt is not None
        assert new_instructions in refreshed.compiled_system_prompt


# --- Context status tests ---

def _stored_tool_content(name: str) -> str:
    """Snapshot content in the stored shape (compact JSON list of definitions).

    Only the fields the projection reads, with a short name so tests can assert exact
    byte counts by hand. The real pydantic-ai shape is pinned by
    TestToolDefinitionSnapshotShape.
    """
    return json.dumps(
        [{"name": name, "description": "", "parameters_json_schema": {}}], separators=(",", ":")
    )


@pytest.mark.usefixtures("seed_stub_snapshots")
class TestGetContextStatus(DBTestBase):
    """get_context_status — composition from the latest in-window response.

    Message rows are inserted directly: these tests are about which row is chosen and how
    its recorded window start is interpreted. Persistence itself is covered by
    tests/messages/test_messages.py.
    """

    @pytest_asyncio.fixture(autouse=True)
    async def agent(self, session: AsyncSession):
        self.record = await commit_and_detach_fixture_record(
            session,
            AgentRecord(name="status-agent", agent_config=SAMPLE_AGENT_CONFIG, system_instructions=""),
        )
        # In prod the first message's window_start_msg_id is its own id; rows built in
        # these tests reference it by default to model an uncompacted window.
        self.first_message_id = str(uuid.uuid4())

    async def store(self, *rows) -> None:
        self.session.add_all(rows)
        await self.session.commit()

    async def status(self, context_window_start: int = 0) -> ContextStatus:
        return await get_context_status(self.ro_session, self.record.id, context_window_start)

    async def test_no_messages_reports_nothing_measured(self):
        assert await self.status() == ContextStatus(
            total_tokens=None, sys_prompt_bytes=None, tool_defs_bytes=None, stale=False
        )

    async def test_uses_latest_response_not_latest_row(self):
        """Mid-run the newest row is a tool-return ModelRequest with no usage data."""
        start = self.first_message_id
        await self.store(
            make_message_record(self.record.id, 0, 100, window_start_id=start, message_id=start),
            make_message_record(self.record.id, 1, None, window_start_id=start),
            make_message_record(self.record.id, 2, 250, window_start_id=start),
            make_message_record(self.record.id, 3, None, window_start_id=start),
        )

        assert await self.status() == ContextStatus(
            total_tokens=250, sys_prompt_bytes=0, tool_defs_bytes=0, stale=False
        )

    async def test_response_before_the_window_start_is_not_reported(self):
        """After /clear the old rows remain but describe a window that no longer exists."""
        start = self.first_message_id
        cleared_start = str(uuid.uuid4())
        await self.store(
            make_message_record(self.record.id, 0, 100, window_start_id=start, message_id=start),
            make_message_record(self.record.id, 1, None, window_start_id=start, message_id=cleared_start),
        )

        assert await self.status(context_window_start=1) == ContextStatus(
            total_tokens=None, sys_prompt_bytes=None, tool_defs_bytes=None, stale=False
        )

    async def test_stale_when_the_measurement_predates_a_compaction(self):
        """Compaction advances the window start; rows persisted before it keep the old id,
        so their totals describe a window that no longer exists."""
        start = self.first_message_id
        compacted_start = str(uuid.uuid4())
        await self.store(
            make_message_record(self.record.id, 0, 100, window_start_id=start, message_id=start),
            make_message_record(self.record.id, 1, None, window_start_id=start, message_id=compacted_start),
            make_message_record(self.record.id, 2, 500, window_start_id=start),
        )

        status = await self.status(context_window_start=1)

        assert status.total_tokens == 500
        assert status.stale is True

    async def test_byte_counts_come_from_the_measured_row_not_the_live_prompt(self):
        """A compaction recompile writes a new prompt; an older response was measured
        against the old one, so prompt and total must be read from the same row."""
        old_prompt, new_prompt = "old prompt", "x" * 500
        old_hash = hashlib.sha256(old_prompt.encode("utf-8")).hexdigest()
        new_hash = hashlib.sha256(new_prompt.encode("utf-8")).hexdigest()
        self.session.add_all([
            SystemPromptSnapshot(id=old_hash, content=old_prompt),
            SystemPromptSnapshot(id=new_hash, content=new_prompt),
        ])
        await self.session.flush()  # INSERT snapshots before rows that reference them
        start = self.first_message_id
        await self.store(
            make_message_record(
                self.record.id, 0, 100, window_start_id=start, message_id=start, system_prompt_hash=old_hash
            ),
            make_message_record(self.record.id, 1, None, window_start_id=start, system_prompt_hash=new_hash),
        )

        status = await self.status()

        assert status.total_tokens == 100
        assert status.sys_prompt_bytes == len(old_prompt.encode("utf-8"))

    async def test_each_agent_reports_its_own_tool_snapshot(self):
        """Shared snapshot rows are resolved per agent — no bleed between agents."""
        short_content = _stored_tool_content("short")
        long_content = _stored_tool_content("muchlongername")
        short_hash = hashlib.sha256(short_content.encode("utf-8")).hexdigest()
        long_hash = hashlib.sha256(long_content.encode("utf-8")).hexdigest()
        self.session.add_all([
            ToolDefinitionSnapshot(id=short_hash, content=short_content),
            ToolDefinitionSnapshot(id=long_hash, content=long_content),
        ])
        await self.session.flush()  # INSERT snapshots before rows that reference them
        other = await commit_and_detach_fixture_record(
            self.session,
            AgentRecord(name="other-agent", agent_config=SAMPLE_AGENT_CONFIG, system_instructions=""),
        )
        start, other_start = self.first_message_id, str(uuid.uuid4())
        await self.store(
            make_message_record(
                self.record.id, 0, 100, window_start_id=start, message_id=start, tool_definition_hash=short_hash
            ),
            make_message_record(
                other.id, 0, 200, window_start_id=other_start, message_id=other_start, tool_definition_hash=long_hash
            ),
        )

        assert (await self.status()).tool_defs_bytes == 7  # "short" + "{}"
        assert (await get_context_status(self.ro_session, other.id, 0)).tool_defs_bytes == 16  # "muchlongername" + "{}"


EXPECTED_TOOL_DEFINITION_FIELDS = {
    "name",
    "parameters_json_schema",
    "description",
    "outer_typed_dict_key",
    "strict",
    "sequential",
    "kind",
    "metadata",
    "timeout",
    "defer_loading",
    "unless_native",
    "with_native",
    "tool_kind",
    "return_schema",
    "include_return_schema",
    "toolset_id",
    "capability_id",
}


class TestToolDefsWireBytes:
    """tool_defs_wire_bytes — projection to the fields providers actually receive."""

    def test_counts_only_wire_fields(self):
        content = json.dumps(
            [
                {
                    "name": "fetch",
                    "description": "Fetch a page.",
                    "parameters_json_schema": {"type": "object"},
                    "return_schema": {"type": "string"},
                }
            ],
            separators=(",", ":"),
        )

        # "fetch" + "Fetch a page." + {"type":"object"} — the return schema is not counted.
        assert tool_defs_wire_bytes("snap", content) == 35

    def test_description_none_counts_as_empty_but_missing_raises(self):
        none_description = json.dumps(
            [{"name": "t", "description": None, "parameters_json_schema": {}}], separators=(",", ":")
        )
        missing_description = json.dumps([{"name": "t", "parameters_json_schema": {}}], separators=(",", ":"))

        assert tool_defs_wire_bytes("snap", none_description) == 3  # "t" + {}
        with pytest.raises(RuntimeError, match=r"snap.*description"):
            tool_defs_wire_bytes("snap", missing_description)

    def test_missing_field_fails_loudly_naming_snapshot_and_field(self):
        content = json.dumps([{"name": "t", "description": "d"}], separators=(",", ":"))

        with pytest.raises(RuntimeError, match=r"snapshot-1.*parameters_json_schema"):
            tool_defs_wire_bytes("snapshot-1", content)

    def test_unicode_counts_bytes_not_characters(self):
        content = json.dumps(
            [{"name": "t", "description": "a → b", "parameters_json_schema": {}}], separators=(",", ":")
        )

        assert tool_defs_wire_bytes("snap", content) == 1 + len("a → b".encode("utf-8")) + 2


class TestToolDefinitionSnapshotShape(DBTestBase):
    """Pins pydantic-ai's ToolDefinition shape and the projection's field list together.

    Snapshots persist dataclasses.asdict(ToolDefinition) via the real serializer. If
    pydantic-ai renames or adds a field, this test is the tripwire: it fails with the
    expected field set right here instead of the meter drifting quietly in production.
    """

    async def test_stored_shape_and_wire_projection(self):
        tool = ToolDefinition(
            name="web_fetch",
            description="Fetch a page.",
            parameters_json_schema={"type": "object", "properties": {"url": {"type": "string"}}},
            return_schema={"type": "string", "description": "x" * 500},
        )

        snapshot_id = await _ensure_tool_definition_snapshotted(self.session, [tool])
        snapshot = await self.session.get(ToolDefinitionSnapshot, snapshot_id)
        assert snapshot is not None
        content = snapshot.content

        assert set(json.loads(content)[0]) == EXPECTED_TOOL_DEFINITION_FIELDS
        assert tool_defs_wire_bytes(snapshot_id, content) == 9 + 13 + len(
            json.dumps(tool.parameters_json_schema, separators=(",", ":")).encode("utf-8")
        )
        # The return schema is stored (needed for context reconstruction) but never counted.
        assert "x" * 500 in content
