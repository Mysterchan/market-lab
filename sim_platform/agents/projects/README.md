# Static Agent project inspection

`inspection.py` receives captured UTF-8 text values. It checks Python syntax,
`agent.py:create_agent(context)`, dependency requirements and optional Python settings.
Newer syntax can be deferred until `syntax.py` checks it with the selected interpreter
during environment preparation. Static acceptance alone is not complete validation.
It never imports the project, evaluates annotations/decorators, executes the factory,
resolves a model key or starts an experiment.

File traversal/copying belongs in [persistence/agents](../../persistence/agents/README.md).
Saved revision orchestration belongs in
[application/agents](../../application/agents/README.md). User Agent implementation
still imports [SDK](../../sdk/README.md), not these internal classes.

The format and Platform examples are in [Agent projects](../../../docs/agent-development/projects.md).
