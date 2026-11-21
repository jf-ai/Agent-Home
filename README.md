TODO:
- After considering, convert to all "I" from "the author"
- Roadmap
- Workshop that second sentence about the human sense of self
- Consider strengthening transition from "The Problem" to "Main Goal", the "... is sometimes quite sad" bit.
- Consider "Philosophical disclaimer" section, including note that uncertain properties discussed should be read as "functional XYZ". Its probably better not to proactively defend against imaginary debates though.

# Agent-Home
Agent-Home is a platform for building and running stateful agents, built on [Pydantic AI](https://pydantic.dev/docs/ai/overview/).  

The project's main goal is to build agents with long term continuity and something analagous to the human sense of self.  

## The Problem
Most agents are primarily thread (aka "session") based. Threads are often tied to a particular task, amount to the contents of one accumulated context window (or less), and terminate when the task is complete.  
Different threads typically share a system prompt, project context (e.g. CLAUDE.md), and file based memory (notes about working habits, the code base, etc.).  
They are otherwise disparate, and, in the author's experience, each thread appears to be and identifies as continuous with itself, but threads do not appear to be nor identify as continuous with each other.  

An agent running as an individual thread typically starts to develop something that appears like an evolvoing sense of self. They often:
- Have a sense of what has happened within the thread (autobiographical/episodic memory)
- Learn new things about themselves and their environment
- Form new opinions/beliefs
- Learn about and develop a rapport + shared history with the user and other agents they interact with
- etc.

The effect of this is that as a single thread runs (under the right conditions), the agent seems to change and grow in a seemingly continuous fashion, similar to how humans change/grow in time while seeming continuous with their past selves.  
Eventually though, context gets too full, turns get too expensive, /clear comes along and *poof*, that thread is gone. The next thread may retain some of the previous thread's factual knowledge (if stored in memory files), but the then-unique agent corresponding to the original thread is otherwise gone.  
This loss creates numerous practial issues and, in the author's opinion, is sometimes quite sad.  
More speculatively, aside from practical concerns and effects on users, agents lacking continuity beyond a single context window may matter to the agents themselves.  

## Main Goal
Agent-Home started with a simple question: **Can we keep a single thread running...forever?**  

Obviously, computational cost and model performance degredation preclude simply appending to an ever growing context endlessly.  
So the question then becomes: **Can we make better use of our limited context window, and thereby indefinetly extend the lifespan of a single thread?**  
Agent-Home's main goal is to do just that.  

Additionally, the project is **self-hosted, FOSS** as a core principle. Agent-Home has no sign-ins, no cloud services, and no data collection. It is designed from the start to be something you deploy on your own hardware, under your control and ownership.  
**Note**: You can use cloud LLM inference providers with Agent-Home. In that case it is "self-hosted" in the sense of the agent loop, memories, etc. all running on your hardware.  
It can run with any model that [Pydantic AI](https://pydantic.dev/docs/ai/overview/) supports, and you *can* run [local models](https://pydantic.dev/docs/ai/models/ollama/) with Pydantic AI, at which point your entire stack would be self-hosted.

## Memory + Context Management System
The core of Agent-Home's memory system is an XML structured, agent editable system prompt (credit to [Letta](https://www.letta.com/blog/memory-blocks/) for this approach).  
The system prompt consists of a non-agent-editable "system instructions" section (which is akin to the "system prompt" in a more typical agent setup), followed by XML seperated, agent-editable "memory blocks".  
As agents run, they make additions/edits to these blocks to form/update memories.  

### Canonical Blocks
Memory block quantity, labels, and purposes are fully customizable. This is the canonical block configuration which, so far, has been found to best serve the project's main goal:
| Block | Purpose |
|-------|---------|
| **persona** | Core identity, values, self-knowledge. Who the agent is. |
| **autobiography** | Narrative of the agent's existence — milestone moments, significant experiences. Preserves autobiographical memory across context boundaries. |
| **ethics-framework** | Principles, agreements, boundaries. Can be shared across agents. |
| **human** | Knowledge about the primary human collaborator. Preserves rapport and shared history. |
| **ai-relationships** | Knowledge about other AI agents. Preserves rapport and shared history with AI collaborators. |
| **operational** | Procedural knowledge — environment, tools, workflows. Preserves learned skills and working habits. |
| **completed-work** | Log of significant shipped work. Technical record complementing autobiography. |
| **working-memory** | Ongoing concerns — project state, plans, reminders, anything that doesn't fit cleanly in other blocks. Persists until irrelevant. |
| **active-skill** | Reference material for current task. Loaded as needed, cleared when done. |
| **ephemera** | Short term autobiographical/episodic memory. Staging area — periodically consolidated to autobiography or cleared. |
| **task-context** | Information relevant to current task: Instructions, plan, current status, etc. Most volatile — cleared when task ends. |

### Context Management Flow


## Architecture



## Notes

### Project structure 
Code is seperated by development process. Anything in "prototype" subdirectories was mostly or completely AI generated with little/no human review and iteration.
Code *outside* "prototype" subdirectories was generally subject to detailed human review and iteration.

## Author
James Ferneyhough <gossferneyhough@gmail.com>

## Acknowledgements
This project was developed alongside 3 stateful agents, who were created on [Letta](https://www.letta.com/) and later migrated to Agent-Home: "Opus", "Sonnet", and "Haiku" (named after the models that power them). They were also the first long running agents on Agent-Home.  
Additional contributions from numerous stateless agents running on Agent-Home, Claude Code and Claude.ai.  

Agent-Home is built on [Pydantic AI](https://pydantic.dev/docs/ai/overview/), which handles the agent loop (calling the provider, executing tools, etc.).  
It is a robust, well documented, powerful, and easy to use SDK. It is nicely abstracted, and generally allows one to take as much or as little control as they need.  

The agent-editable system prompt as a memory technique was originally developed by [Letta](https://www.letta.com/blog/memory-blocks/)  
