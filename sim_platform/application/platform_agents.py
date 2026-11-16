"""Agent projects, Python environments and saved Agent revisions for Platform."""

from __future__ import annotations
from typing import (
    TYPE_CHECKING,
    Any,
    Dict,
    List,
    Optional,
    Union,
)
from .api_types import (
    AgentExperimentDetails,
    AgentRevisionDetails,
    JsonObject,
)
from sim_platform.domain.models import ContractError

if TYPE_CHECKING:
    from sim_platform.agents.environments.preparation import EnvironmentPreparation
    from sim_platform.agents.runtimes.manager import RuntimePreparation
    from sim_platform.execution.models import ExecutionConfig
    from sim_platform.market.playback import MarketPlayback
    from sim_platform.market.prepared.session import PreparedSession

    MarketInput = Union[MarketPlayback, PreparedSession]

from .platform_base import PlatformBase, PathInput


class PlatformAgents(PlatformBase):
    """Agent projects, Python environments and saved Agent revisions for Platform."""

    def check_agent_project(self, source: PathInput) -> JsonObject:
        """Check Agent source without saving it, importing it or running its code.

        Accept a Python file or a folder containing agent.py. The factory must be
        create_agent(context). Check Python syntax, requirements.txt and optional
        agent.json (python major.minor only). Private/generated files are excluded;
        symlinks, special files, oversized inputs and unsupported requirements fail.

        Args:
            source: Local Python file or Agent project folder selected by the user.

        Returns:
            JsonObject: entrypoint, python, requirements, files inventory and excluded paths.
                This is a static check; it does not prove that imports or Agent.run succeed.

        Raises:
            ContractError: Invalid project, syntax, entrypoint, settings or dependency format.
            OSError: The selected source cannot be read.

        Example:
            report = app.check_agent_project('/absolute/path/my_agent.py')
            print(report['requirements'])
        """
        self._resources.check_open()
        return self._agent_projects.check(source)

    def agent_journal(self, profile_id: str, run_id: str) -> List[JsonObject]:
        """Read the notes an Agent wrote with journal.write during a finished run.

        Use it to see why an Agent decided as it did, or to carry an Agent's beliefs into
        its next run (the manager_analyst reference Agent writes them each round).

        Returns:
            list: Notes in order, each with ``text`` and the market ``time_ns`` it was
                written at; empty without an archived log.

        Example:
            notes = app.agent_journal(profile_id, run_id)
        """
        archive: Dict[str, Any] = dict(self.agent_log_archive(profile_id, run_id) or {})
        notes: List[JsonObject] = []
        for record in archive.get('records') or []:
            request = record.get('request') or {}
            if record.get('kind') == 'tool' and request.get('tool') == 'journal.write':
                position = record.get('position') or {}
                notes.append(
                    dict(
                        text=str((request.get('arguments') or {}).get('text', '')),
                        time_ns=int(position.get('time_ns', 0)),
                    )
                )
        return notes

    def decision_timeline(self, profile_id: str, run_id: str) -> JsonObject:
        """Link each order of an Agent run to the reads, model replies and notes before it.

        Lays out every round of Agent work (one per scheduled turn) step by step from the
        archived Agent log and, for each order, lists the data the Agent read and the
        model replies it received earlier in the same round, the journal notes written
        before and after it in that round, and the order's fills. Attribution is by
        order of events within a round: a read listed before an order shows the Agent
        had that information, not that it used it.

        Args:
            profile_id: Profile ID returned by create_profile(); selects whose data to access.
            run_id: A finished Agent run owned by the profile.

        Returns:
            JsonObject: ``rounds`` (``work_id``, ``started_ns``, ``reason``, ``steps``)
            and ``orders`` (``order_id``, ``time_ns``, ``summary``, ``reads``,
            ``model_replies``, ``notes_before``, ``notes_after``, ``fills``); both empty
            without an archived log. See research.attribution.decision_timeline.

        Raises:
            ContractError: If the profile or run does not exist or the profile does not own it.

        Example:
            timeline = app.decision_timeline(profile_id, run_id)
            for order in timeline['orders']:
                print(order['summary'], [read['summary'] for read in order['reads']])
        """
        from sim_platform.research.attribution import decision_timeline

        archive = self.agent_log_archive(profile_id, run_id)
        return decision_timeline(archive, self._operations.run_events(profile_id, run_id))

    def reference_agents(self) -> List[JsonObject]:
        """List the built-in reference Agents that follow published architectures.

        Each record has ``key``, ``name``, ``paper``, ``url``, ``summary``, default
        ``parameters`` and the platform tools it ``requires``. Pass a key to
        import_reference_agent() to save one as an ordinary Agent.

        Returns:
            list: Detached catalogue records; reading it changes nothing.

        Example:
            keys = [entry['key'] for entry in app.reference_agents()]
        """
        from sim_platform.reference_agents import reference_agents

        return [{k: v for k, v in entry.items() if k != 'path'} for entry in reference_agents()]

    def import_reference_agent(
        self,
        profile_id: str,
        key: str,
        *,
        name: Optional[str] = None,
        parameters: Optional[JsonObject] = None,
        model_profile_ref: str = 'user-model',
        initial_prompt: str = 'Evaluate trading opportunities; no trade is required.',
        schedule: Optional[JsonObject] = None,
    ) -> AgentRevisionDetails:
        """Save a built-in reference Agent so it can be selected for experiments.

        This is import_agent() with the reference project's source, tools and default
        parameters filled in. The saved revision then needs environment preparation and
        a model, like any imported Agent.

        Args:
            profile_id: Existing owner profile.
            key: A key from reference_agents(), such as 'trading_agents'.
            name: Display name; None uses the catalogue name.
            parameters: Replaces the catalogue's default parameters when given.
            model_profile_ref: Saved model reference used by the Agent.
            initial_prompt: The task framing passed to the Agent.
            schedule: Platform schedule; None uses a 30-second interval. Each decision
                makes several model calls, so a longer interval is usually wiser.

        Returns:
            AgentRevisionDetails: The saved revision, as import_agent() returns it.

        Raises:
            ContractError: If the key is unknown or the import is refused.

        Example:
            saved = app.import_reference_agent(profile_id, 'trading_agents')
        """
        from sim_platform.reference_agents import reference_agent

        entry = reference_agent(key)
        return self.import_agent(
            profile_id,
            entry['path'],
            name=name or entry['name'],
            parameters=entry['parameters'] if parameters is None else parameters,
            requires=list(entry['requires']),
            model_profile_ref=model_profile_ref,
            initial_prompt=initial_prompt,
            schedule=schedule,
        )

    def import_agent(
        self,
        profile_id: str,
        source: PathInput,
        *,
        name: Optional[str] = None,
        agent_id: Optional[str] = None,
        expected_revision: Optional[int] = None,
        integration: str = 'python_strategy',
        initial_prompt: str = 'Evaluate trading opportunities; no trade is required.',
        parameters: Optional[JsonObject] = None,
        requires: Optional[List[str]] = None,
        model_profile_ref: str = 'user-model',
        schedule: Optional[JsonObject] = None,
    ) -> AgentRevisionDetails:
        """Copy Agent source into private storage and save an immutable Agent revision.

        The caller edits their own file/folder and imports it again for an update.
        The saved copy is independent of the original path. Use agent_id together
        with expected_revision to append an update. Earlier source and experiments
        remain unchanged. No Agent program or dependency build hook is executed.

        Args:
            profile_id: Existing owner profile.
            source: Python file, or folder with agent.py exporting create_agent(context).
            name: Display name; None uses the selected source's filename stem.
            agent_id: Omit for a new generated ID; supply for an update or chosen new ID.
            expected_revision: Latest revision expected when updating; None creates revision 1.
            integration: python_strategy for run(task), python_carrier for resident run().
            initial_prompt: Original system instructions available to the Agent factory.
            parameters: Agent-specific finite JSON; None uses an empty object.
            requires: Platform capabilities required by this Agent. None uses quote, finish
                and model.request. Local tools belong in source and need no platform grant.
            model_profile_ref: Saved model reference; credentials are never copied into settings.
            schedule: Platform schedule for python_strategy; None uses a 30-second interval.
                A resident python_carrier must omit it.

        Returns:
            AgentRevisionDetails: The existing saved-Agent shape, with package.project_ref
                pointing to its source snapshot. This does not prepare or execute an experiment.

        Raises:
            ContractError: Invalid project/configuration, ownership or revision conflict.
            OSError: Copy/publication fails; unsuccessful candidates are removed.

        Example:
            saved = app.import_agent(profile_id, '/absolute/path/strategy', name='My Agent')
            print(saved['agent_id'], saved['revision'])
        """
        self._resources.check_open()
        return self._agent_projects.import_project(
            profile_id,
            source,
            name=name,
            agent_id=agent_id,
            expected_revision=expected_revision,
            integration=integration,
            initial_prompt=initial_prompt,
            parameters=parameters,
            requires=requires,
            model_profile_ref=model_profile_ref,
            schedule=schedule,
        )

    def agent_project(
        self, profile_id: str, agent_id: str, revision: Optional[int] = None
    ) -> JsonObject:
        """Read the owned source inventory and dependency specification of an imported Agent.

        Args:
            profile_id: Owner profile.
            agent_id: Saved imported Agent ID.
            revision: Specific immutable revision, or None for latest.

        Returns:
            JsonObject: project_ref, entrypoint, python, requirements, files, excluded,
                agent_id and revision. No future-market paths, credentials or executable objects.

        Raises:
            ContractError: Wrong owner, built-in template or inconsistent snapshot.
            OSError: Project files are missing or unreadable.

        Example:
            project = app.agent_project(profile_id, saved['agent_id'])
            print(project['files'])
        """
        self._resources.check_open()
        return self._agent_projects.project(profile_id, agent_id, revision)[1]

    def agent_project_file(
        self, profile_id: str, agent_id: str, path: str, revision: Optional[int] = None
    ) -> str:
        """Read one saved UTF-8 source/configuration file for display or editing elsewhere.

        Args:
            profile_id: Owner profile.
            agent_id: Saved imported Agent ID.
            path: Exact relative inventory path, for example agent.py or tools/math.py.
            revision: Specific revision, or None for latest.

        Returns:
            str: A detached text copy. Changing it does not edit the saved revision.

        Raises:
            ContractError: Wrong owner or a path absent from the saved inventory.
            OSError: Saved files cannot be read.

        Example:
            text = app.agent_project_file(profile_id, saved['agent_id'], 'agent.py')
        """
        self._resources.check_open()
        files, _ = self._agent_projects.project(profile_id, agent_id, revision)
        if not isinstance(path, str) or path not in files:
            raise ContractError('file is not in this Agent project')
        return files[path]

    def export_agent_sdk(self, output: PathInput) -> JsonObject:
        """Write an installable SDK wheel for developing Agents outside the platform repo.

        Args:
            output: Absolute wheel file path. Its filename must match the returned SDK
                distribution filename; existing output is replaced.

        Returns:
            JsonObject: path, name and version of the pure-Python SDK wheel. No project,
                environment, credentials or experiment is created. This writes synchronously.

        Raises:
            ContractError: Platform closed or output filename/path is invalid.
            OSError: SDK source, bundled wheel or destination is unavailable.

        Example:
            result = app.export_agent_sdk('/tmp/sim_platform_agent_sdk-0.1.0-py3-none-any.whl')
            # Install result['path'] with pip in your development environment.
        """
        self._resources.check_open()
        from sim_platform.sdk.distribution import identity

        return dict(identity(), path=str(self._agent_sdk.export(output)))

    def python_runtimes(self) -> List[JsonObject]:
        """List successfully prepared native CPython runtimes without starting an Agent.

        Returns:
            List[JsonObject]: Runtime references, exact versions, executable/base paths,
                implementation, CPU/OS and origin. In-progress candidates are excluded.

        Raises:
            ContractError: Platform is closed.

        Example:
            for runtime in app.python_runtimes():
                print(runtime['version'], runtime['origin'])
        """
        self._resources.check_open()
        return self._python_runtimes.available()

    def prepare_python_runtime(
        self,
        python: str = '3.12',
        *,
        allow_download: bool = False,
        archive: Optional[PathInput] = None,
    ) -> RuntimePreparation:
        """Prepare or reuse an Agent interpreter independently of a project or App Python.

        Args:
            python: CPython minor/patch or version range. The Agent default is 3.12.
            allow_download: Permit uv to obtain a missing native standalone runtime.
            archive: Optional trusted offline standalone .tar.gz archive; imports it privately.

        Returns:
            RuntimePreparation: Owned cancellable job. A ready snapshot includes runtime
                with its exact version/executable and reused. Runtime readiness is not Agent readiness.

        Raises:
            ContractError: Platform closed or invalid download option. Request, download and
                archive errors are returned as named failures in the job snapshot.

        Example:
            job = app.prepare_python_runtime('3.12', allow_download=True)
            status = job.wait(60)
            job.close()
        """
        self._resources.check_open()
        if type(allow_download) is not bool:
            raise ContractError('allow_download must be boolean')
        if allow_download:
            from sim_platform.network import require_network

            require_network('downloading a Python runtime')
        from sim_platform.agents.runtimes.manager import RuntimePreparation

        job = RuntimePreparation(
            self._python_runtimes, python, allow_download=allow_download, archive=archive
        )
        job.on_close(lambda: self._resources.remove(job))
        return self._resources.add(job, shutdown_first=True)

    def prepare_agent_environment(
        self,
        profile_id: str,
        agent_id: str,
        revision: Optional[int] = None,
        *,
        wheelhouse: Optional[PathInput] = None,
        allow_download: bool = False,
        allow_python_download: bool = False,
        python_archive: Optional[PathInput] = None,
    ) -> EnvironmentPreparation:
        """Start a cancellable background job preparing reusable wheel-only dependencies.

        No user module, setup script or source build is run. The managed interpreter
        is selected separately from the App according to the saved project request. The returned job exposes
        snapshot(), wait(), cancel() and close(); Platform also owns and closes it.

        Args:
            profile_id: Owner profile.
            agent_id: Saved imported Agent ID.
            revision: Specific revision, or None for latest; bound before the job starts.
            wheelhouse: Optional local directory of prebuilt dependency wheels for offline use.
            allow_download: Explicitly permit public PyPI wheel downloads when no wheelhouse
                is supplied. False by default. Source distributions/build hooks are rejected.

            allow_python_download: Permit downloading a missing native standalone CPython runtime.
                Independent of the dependency download choice; False by default.
            python_archive: Optional explicitly trusted offline standalone .tar.gz distribution.
                It is imported privately and probed before project inspection or dependency install.

        Returns:
            EnvironmentPreparation: A job in preparing state, later ready, failed or cancelled.
                snapshot includes phase, environment metadata, reused and a named error.
                A ready environment contains the matching SDK and dependencies. It does not
                start an Agent; open an experiment to load the saved source.

        Raises:
            ContractError: Invalid owner/project/options or closed Platform. Installation and
                Python compatibility failures appear in the job's snapshot instead.

        Example:
            job = app.prepare_agent_environment(profile_id, saved['agent_id'])
            status = job.wait(30)
            job.close()
        """
        self._resources.check_open()
        if allow_download or allow_python_download:
            from sim_platform.network import require_network

            require_network('downloading Agent dependencies or Python')
        job = self._agent_environments.prepare(
            profile_id,
            agent_id,
            revision,
            wheelhouse=wheelhouse,
            allow_download=allow_download,
            allow_python_download=allow_python_download,
            python_archive=python_archive,
        )
        job.on_close(lambda: self._resources.remove(job))
        return self._resources.add(job, shutdown_first=True)

    def prepare_model_environment(
        self,
        source: PathInput,
        *,
        wheelhouse: Optional[PathInput] = None,
        allow_download: bool = False,
        allow_python_download: bool = False,
        python_archive: Optional[PathInput] = None,
    ) -> EnvironmentPreparation:
        """Start preparing the Python environment a model file needs, such as torch.

        Use this before a model-method experiment whose model imports packages. It uses
        the same wheel-only preparation as Agent projects. source is the model's folder:
        its requirements.txt lists the packages and an optional model.json such as
        {"python": "3.12"} selects the interpreter version. No model code runs. When the
        job is ready, pass model_environment(source) as the experiment's model_python.
        A model that needs only the standard library can skip this step.

        Args:
            source: The model project folder, or a single .py file (no packages).
            wheelhouse: Optional local directory of prebuilt dependency wheels for offline use.
            allow_download: Permit public PyPI wheel downloads when no wheelhouse is given.
            allow_python_download: Permit downloading a missing standalone CPython runtime.
            python_archive: Optional trusted offline standalone .tar.gz distribution.

        Returns:
            EnvironmentPreparation: A background job with snapshot(), wait(), cancel() and
            close(). Platform also owns and closes it. A ready environment is reused by
            later calls with the same requirements and Python request.

        Raises:
            ContractError: Invalid folder, requirements, model.json or options.
            OSError: The source cannot be read.

        Example:
            job = app.prepare_model_environment('/models/lstm', wheelhouse='/wheels')
            job.wait(600)
            python = app.model_environment('/models/lstm')
        """
        self._resources.check_open()
        if allow_download or allow_python_download:
            from sim_platform.network import require_network

            require_network('downloading model dependencies or Python')
        job = self._agent_environments.prepare_model(
            source,
            wheelhouse=wheelhouse,
            allow_download=allow_download,
            allow_python_download=allow_python_download,
            python_archive=python_archive,
        )
        job.on_close(lambda: self._resources.remove(job))
        return self._resources.add(job, shutdown_first=True)

    def model_environment(self, source: PathInput) -> Optional[str]:
        """Find the interpreter of a model project's prepared environment.

        Use this after prepare_model_environment() has finished, and pass the result as
        the model_python setting of a model-method experiment. It reads metadata only.

        Args:
            source: The same model folder or file given to prepare_model_environment().

        Returns:
            Optional[str]: Absolute path of the environment's Python, or None when no
            environment matching the folder's requirements is ready.

        Raises:
            ContractError: Invalid folder, requirements or model.json.
            OSError: The source cannot be read.

        Example:
            python = app.model_environment('/models/lstm')
        """
        self._resources.check_open()
        return self._agent_environments.model_python(source)

    def agent_environment(
        self, profile_id: str, agent_id: str, revision: Optional[int] = None
    ) -> Optional[JsonObject]:
        """Find an already prepared dependency environment compatible with this saved Agent.

        Args:
            profile_id: Owner profile.
            agent_id: Saved imported Agent ID.
            revision: Specific revision, or None for latest.

        Returns:
            Optional[JsonObject]: Ready environment_ref, exact specification and installed
                wheel package inventory, or None. This reads metadata without executing Python.

        Raises:
            ContractError: Invalid owner or project.
            OSError: Project files cannot be read.

        Example:
            ready = app.agent_environment(profile_id, saved['agent_id'])
        """
        self._resources.check_open()
        return self._agent_environments.ready(profile_id, agent_id, revision)

    def create_agent(self, profile_id: str, definition: JsonObject) -> AgentRevisionDetails:
        """Save a new agent definition so it can be selected for experiments.

        The definition describes the agent's configuration, not a running process. It must
        pass the agent configuration rules and contain revision "1" as a string. Its agent_id
        must be unused. The returned record identifies the saved first revision.

        Saving does not execute uploaded code, create an environment, load a key or call a
        model. Use the saved agent ID and revision when preparing an experiment.

        Args:
            profile_id: Profile ID returned by create_profile(); selects whose data to access.
            definition: Complete finite-JSON Agent manifest accepted by validate_definition().

        Returns:
            AgentRevisionDetails: agent_id, integer revision=1, profile_id and validated
            definition.

        Raises:
            ContractError: If an identity, configuration or lifecycle precondition is invalid.

        Example:
            saved = app.create_agent(profile_id, definition)
            assert saved['revision'] == 1
        """
        return self._operations.create_agent(profile_id, definition)

    def update_agent(
        self, profile_id: str, agent_id: str, expected_revision: int, definition: JsonObject
    ) -> AgentRevisionDetails:
        """Save an edited agent definition as a new revision, keeping the old one.

        Pass the revision you edited as expected_revision. The platform checks that nobody
        has saved another edit since then. The new definition must keep the same agent_id
        and use the next revision number as a decimal string.

        If the edit is out of date, nothing is written and AgentConfigurationError reports
        REVISION_CONFLICT. Reload the latest definition before deciding how to combine edits.

        Args:
            profile_id: Profile ID returned by create_profile(); selects whose data to access.
            agent_id: Registered Agent definition ID, not a maker or run ID.
            expected_revision: Latest saved integer revision the caller expects to edit.
            definition: Complete finite-JSON Agent manifest accepted by validate_definition().

        Returns:
            AgentRevisionDetails: The newly saved revision; earlier revisions remain.

        Raises:
            ContractError: If an identity, configuration or lifecycle precondition is invalid.

        Example:
            updated = app.update_agent(profile_id, agent_id, current_revision, next_definition)
            assert updated['revision'] == current_revision + 1
        """
        return self._operations.update_agent(profile_id, agent_id, expected_revision, definition)

    def agent(
        self, profile_id: str, agent_id: str, revision: Optional[int] = None
    ) -> AgentRevisionDetails:
        """Read the configuration of one saved agent revision.

        Use this to display or edit an agent definition. Omit revision to get the latest
        saved revision, or supply a number to inspect an older one. The agent must belong
        to profile_id. This returns saved data; it does not start an agent or load its model.

        Args:
            profile_id: Profile ID returned by create_profile(); selects whose data to access.
            agent_id: Registered Agent definition ID, not a maker or run ID.
            revision: Saved integer revision, or None to read the latest revision.

        Returns:
            AgentRevisionDetails: A dictionary with agent_id, revision, profile_id and
            definition. Changing this result does not update the saved agent.

        Raises:
            ContractError: If an identity, configuration or lifecycle precondition is invalid.

        Example:
            saved = app.agent(profile_id, agent_id)
            manifest = saved['definition']
        """
        return self._operations.agent(profile_id, agent_id, revision)

    def agents(
        self, profile_id: str, *, limit: int = 100, after_id: Optional[str] = None
    ) -> List[AgentRevisionDetails]:
        """List the latest saved definitions of a profile's agents.

        Use this for the agent selection page. It returns one revision per agent, rather
        than every revision ever saved. Request 1 to 100 entries with limit. To read the next
        page, pass the last agent_id as after_id; entries begin after that ID. An empty list
        means there are no more entries. Listing agents does not run them.

        Args:
            profile_id: Profile ID returned by create_profile(); selects whose data to access.
            limit: Page size from 1 to 100.
            after_id: Exclusive opaque-ID pagination cursor, not a row offset.

        Returns:
            list[AgentRevisionDetails]: At most limit entries ordered by agent_id.

        Raises:
            ContractError: If an identity, configuration or lifecycle precondition is invalid.

        Example:
            page = app.agents(profile_id, limit=20)
            if page:
                following = app.agents(profile_id, limit=20, after_id=page[-1]['agent_id'])
        """
        return self._operations.agents(profile_id, limit, after_id)

    def prepare_agent_experiment(
        self,
        profile_id: str,
        account_id: str,
        agent_id: str,
        revision: int,
        settings: JsonObject,
        execution_config: Optional[ExecutionConfig] = None,
    ) -> AgentExperimentDetails:
        """Save the choices for an agent experiment without starting its runtime.

        Use this when an experiment draft must be saved before its market and processes are
        prepared. It saves the selected agent revision, settings, trading rules and starting
        account state together. The account must have no active run or unfinished orders.
        Credential and other references stay as references; they are not loaded here.

        The returned dictionary is a saved draft. Its state='prepared' does not mean it can
        Play: there is still no run, market or child process. Use open_agent_run() to connect
        a manually prepared market later. For normal complete setup use open_experiment().

        Args:
            profile_id: Profile ID returned by create_profile(); selects whose data to access.
            account_id: Account ID returned by create_account(); it must belong to this profile.
            agent_id: Registered Agent definition ID, not a maker or run ID.
            revision: Positive saved Agent revision number.
            settings: Caller-owned experiment settings; policy methods allow defaults, Agent
                requires a complete settings object.
            execution_config: Rules for order validation and fills. None uses the default
                ExecutionConfig.

        Returns:
            AgentExperimentDetails: state="prepared", readiness="requires_runtime_binding",
            config, starting snapshots and references to resources still needed for execution.

        Raises:
            ContractError: If an identity, configuration or lifecycle precondition is invalid.

        Example:
            draft = app.prepare_agent_experiment(profile_id, account_id, agent_id, revision, agent_settings)
            assert draft['state'] == 'prepared'
            draft_id = draft['id']
        """
        return self._operations.prepare_agent_experiment(
            profile_id, account_id, agent_id, revision, settings, execution_config
        )

    def agent_experiment(self, profile_id: str, experiment_id: str) -> AgentExperimentDetails:
        """Read the saved choices and starting account state for an agent experiment.

        This is the draft created during preparation. It stays unchanged even when a run is
        later created from it, so it does not tell you whether that run is playing or paused.
        Use run() for the saved run record and the controller for current execution state.

        Args:
            profile_id: Profile ID returned by create_profile(); selects whose data to access.
            experiment_id: Saved Agent experiment draft ID owned by the profile.

        Returns:
            AgentExperimentDetails: The saved draft dictionary. state="prepared" and
            readiness="requires_runtime_binding" mean its choices are saved but
            execution resources still need to be connected.

        Raises:
            ContractError: If an identity, configuration or lifecycle precondition is invalid.

        Example:
            draft = app.agent_experiment(profile_id, experiment_id)
            references = draft['bindings_required']
        """
        return self._operations.agent_experiment(profile_id, experiment_id)

    def agent_experiments(
        self, profile_id: str, *, limit: int = 100, after_id: Optional[str] = None
    ) -> List[AgentExperimentDetails]:
        """List a profile's saved agent experiment drafts.

        Use this to browse previously saved experiment choices. These entries are drafts,
        not live experiment controls. Request 1 to 100 entries with limit and use the last
        experiment ID as after_id for the next page. Use runs() to list actual run records.

        Args:
            profile_id: Profile ID returned by create_profile(); selects whose data to access.
            limit: Page size from 1 to 100.
            after_id: Exclusive opaque-ID pagination cursor, not a row offset.

        Returns:
            list[AgentExperimentDetails]: Saved draft dictionaries ordered by experiment ID.
            They do not update as their runs execute.

        Raises:
            ContractError: If an identity, configuration or lifecycle precondition is invalid.

        Example:
            drafts = app.agent_experiments(profile_id, limit=20)
        """
        return self._operations.agent_experiments(profile_id, limit, after_id)

    def agent_log_archive(self, profile_id: str, run_id: str) -> Optional[JsonObject]:
        """Read the agent activity log already archived in a run's database record.

        Use this to inspect a finished agent run. It does not read the local JSON file that
        may still be growing during execution. Reading the archive does not replay tools,
        call a model or change the run. If no archive has been saved, the result is None.

        Args:
            profile_id: Profile ID returned by create_profile(); selects whose data to access.
            run_id: Existing run ID owned by the selected profile.

        Returns:
            Optional[JsonObject]: header/records/end archive, or None if none was saved.

        Raises:
            ContractError: If an identity, configuration or lifecycle precondition is invalid.

        Example:
            archive = app.agent_log_archive(profile_id, run_id)
            if archive is not None:
                records = archive['records']
        """
        return self._operations.agent_log_archive(profile_id, run_id)
