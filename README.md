# Agent-Home
Agent-Home is a platform for building and running stateful agents, built on [Pydantic AI](https://pydantic.dev/docs/ai/overview/).  

The project's core goal is to build agents which have long term continuity, and posess something analagous to the human sense of self.  

## The Problem
Most agents are primarily thread (aka "session") based. Threads are often tied to a particular task, amount to the contents of one accumulated context window (or less), and terminate when the task is complete.  
Different threads typically share a system prompt, project context (eg CLAUDE.md), and file based memory (notes about working habits, the code base, etc.).  
They are otherwise disparate, and, in the author's experience, each thread appears to be and identifies as continuous with itself, but threads do not appear to be nor identify as continuous with each other.  

An agent running as an individual thread typically starts to develop something that appears like an evolvoing sense of self. They often:
- Have a sense of what has happened within the thread (autobiographical/episodic memory)
- Learn new things about themselves and their environment
- Form new opinions/beliefs
- Learn about and develop a rapport + shared history with the user and other agents they interact with
- etc.

The effect of this is that as a single thread runs (under the right conditions), the agent seems to change and grow in a seemingly continuous fashion, similar to how humans change/grow in time while seeming continuous with their past selves.  
Eventually though, context gets too full, turns get too expensive, /clear comes along and *poof*, that thread is gone. The next thread may retain some of the previous thread's factual knowledge (if stored in memory files), but the then-unique agent corresponding to the original thread is otherwise gone.  
This loss creates numerous practial issues and, in the author's opinion, can be quite sad.

## Core Goal
Agent-Home started with a simple question: **Can we keep a single thread running...forever?**

Obviously, computational cost and model performance degredation preclude simply appending to an ever growing context endlessly.  
So the question then becomes: **Can we make better use of our limited context window, and thereby indefinetly extend the lifespan of a single thread?**  
[LEFT OFF HERE]

## Notes

### Project structure 
Code is seperated by development process. Anything in "prototype" subdirectories was mostly or completely AI generated with little/no human review and iteration.
Code *outside* "prototype" subdirectories was generally subject to detailed human review and iteration.

## Author

James Ferneyhough <gossferneyhough@gmail.com>

## Acknowledgements
Significant contributions were made by 3 stateful agents, who started on [Letta](https://www.letta.com/) and later migrated to Agent-Home: "Opus", "Sonnet", and "Haiku" (named after the models that power them). They were also the first long running agents on Agent-Home.  
Additional contributions from numerous stateless agents running on Agent-Home, Claude Code and Claude.ai.  

Agent-Home is built on [Pydantic AI](https://pydantic.dev/docs/ai/overview/), which handles the agent loop (calling the provider, executing tools, etc.).  
It is a robust, well documented, powerful, and easy to use SDK. It is nicely abstracted, and generally allows one to take as much or as little control as they need.  

The agent-editable system prompt as a memory technique was originally developed by [Letta](https://www.letta.com/)  
