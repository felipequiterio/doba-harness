# Idea for the project

Doba Personal Assistant

Doba personal assistant is an AI agent harness used as a personal assistant for myself.

My idea is to be able to use small local models like qwen-4b parameters so we can run locally and also with fast inference using COT GPU.

In order to compensate the lack of knowledge of the model, we have to create a strong harness that is able to effectively use tools and memory to learn and make the agent more intelligent.

My main idea is that the agent dont need to actually have the information trained in the model if is able to search it, learn then retrieve from memory just like humans.

The harness + model sohuld be very good at reasoning and decision making in order to compensate the lack of base knowledge.

- Model should be local and private.
- Memory
- Tools
- Computer use
- Terminal use
- Browser use
- Internet search
- Learning loop

Disclaimer: I do not expect for example to do very complex tasks like coding, designing, etc. I just want to have a reliable virtual assistant that can help me. These tasks are better with very big models and the state of small models is not good enough for this yet.

The project consists in multiple pilars:

- Harness
- Memory
- Tools
- Agent architecture

## Harness: 

Harness is the core of the project, is how the agent interact with the tools and funcitonalities.

How the harness is going to be built? What architecture should we use? 

A good candidate for framework is pi-mono

Should we use ReAct or Plan and Execute? Should I use both? Is there any other option?

In order to decide how the harness should be architected, some case studies about Claude Code, Codex, OpenClaw and Hermes Agent.

Hermes Agent specifically is interesting as it is said that it can learn trough the harness.


## Agent architecture. Single agent or multiple agents?

Single agent seems a good option as I can try to maintain a single agent and make it more powerful and efficient.

Multi agent looks like the best option but Im afraid is going to make everything confusing and difficult to maintain when there are multiple agents running.

The difference in single agent and multiple agents is that I want to have a sinlge AI that is able to explore and execute tools and tasks. Multiple agents seems to cut that by making very specific use cases. I think that the biggest challenge of using multiple agents is to be able to maintain context and coherence between calls.

I maybe can have the single agent recieve the query and then decide what to do. 

Should he make a plan? 

Sould he just answer? 

Does he need to research something in the internet? 

Does he need to try to recall sometinh from memory? 

How these multiple stages can intertwine and work together? How does a human apporach a problem and how can we make the agent to do the same?

How the harness should be built to be able to handle this?

## Memory

Memory is a critical part of the project as I want the agent to be able to remember and forget things that are relevant to the agent knowledge.

What are the components for the memory? 

A simple short term memory system is good by being able to manage the model context during the conversation.

Now im thinking about a longer memory system where we can use vector database and also graph database to store knowledge.

How the memory would work?

A graph is an interseting structure to store knowledge as it can be used to store relationships between entities.
Also a vectorized graph node could  be used for semantic search and retrieval.

How a general memory/knowledge system is built using vector database and graph database together?

How the memory space will look like with the graph and vector database together?

How the memory should be used? Does the agent need to specifically recall or does the memory is injected in context? How the agent should be able to use the memory?

Does the memory is retrieve at every query? 

Does the agent have to specify if it wants to try to recall something from memory?

How can we make the memory in order to make the agent self improve and learn?

How can we make it remeber or forget things?

How can we setup a graph memory so it can capture different levels of abstractions? 

How the memory sohuld be organized? 

What are the entities and relationships that should be stored?

Should we organize memory like in the great areas of knowledge? Like math, physics, chemistry, biology, history, geography, literature, art, music, etc?

How can we make the memory so it can be used to generate new knowledge?

## Tools

Tools are specific actions the agent can perform.

How tools should be presented to the agent?

The idea of injecting all tools in context seems ok for a small amount of tools but when the number of tools increases it becomes impractical.

One option is to have multiple agents that are specialized in different tasks and access different tools, but I think in makes the system more complex. If one agfent needs to use a tool that is with another agent, in theory it sohuld delegate the task, making more complex.

What is a solution for this? Should I create different toolboxes for the agent and it selects the ones to use? If the toolbox becomes too large, a simple tool query could bloat the context and make the agent to lose focus.

Maybe a tool recommentation via vector search could also work together with the tool selection.

Should the agent be able to create new tools? How can we make it so it can create new tools? 

Tools are essentially functions, how the agent can create if is bad at coding? Should a better model be issued? How to prevent useless tool creation? 

If tool creation is created by using a better model, sohuld we just change the model with the running context or do like a separate agent?

## References

https://www.langchain.com/blog/the-anatomy-of-an-agent-harness