"""Model credential lookups for Platform."""

from __future__ import annotations
from typing import (
    TYPE_CHECKING,
    Optional,
    Union,
)
from .api_types import (
    CredentialPersistence,
)
from sim_platform.domain.models import ContractError

if TYPE_CHECKING:
    from sim_platform.market.playback import MarketPlayback
    from sim_platform.market.prepared.session import PreparedSession
    from sim_platform.models.contracts import ModelProfile, ModelProvider
    from sim_platform.models.credentials import CredentialStore
    from sim_platform.research.agent_controller import AgentResearchController

    MarketInput = Union[MarketPlayback, PreparedSession]

from .platform_base import PlatformBase, PathInput


class PlatformCredentials(PlatformBase):
    """Model credential lookups for Platform."""

    def _credential_store(self) -> CredentialStore:
        """Get the local store used to look up model API keys by reference name.

        The first call creates it and selects macOS Keychain when available. This does not
        read a key yet. Platform keeps the store for later credential calls.

        Returns:
            CredentialStore: Existing or newly allocated local store; no key lookup occurs here.

        Raises:
            ContractError: If an identity, configuration or lifecycle precondition is invalid.
        """
        self._resources.check_open()
        if self._credentials is None:
            from sim_platform.models.credentials import CredentialStore
            import sys

            backend = None
            if sys.platform == 'darwin':
                from sim_platform.models.keychain import MacOSKeychain

                backend = MacOSKeychain()
            self._credentials = CredentialStore(backend)
        return self._credentials

    def set_model_credential(
        self, reference: str, secret: str, *, persistence: CredentialPersistence = 'session'
    ) -> None:
        """Give the platform an API key that an agent can use to call a model.

        Choose a reference name, such as 'my-model-key'. Model settings use that name rather
        than including the key itself. With persistence='session', the key stays in this
        Platform's memory until deletion or close. With 'os', it is saved in the supported
        system credential store, currently macOS Keychain.

        The key is not written into run settings, SQLite, logs or exports. Accessing the
        system credential store can take time and may fail with a system-specific error.

        Args:
            reference: Local credential reference; the reference itself is safe to save in model
                configuration.
            secret: Nonempty API key material supplied by the user; not a reference or file
                path.
            persistence: session for Platform-owned memory; os for an available OS credential
                backend.

        Returns:
            None. A later model setup resolves reference to the selected key.

        Raises:
            ContractError: If an identity, configuration or lifecycle precondition is invalid.

        Example:
            app.set_model_credential('personal', secret_from_user, persistence='session')
        """
        self._credential_store().put(reference, secret, persistence=persistence)

    def load_model_credential(
        self,
        reference: str,
        path: PathInput,
        *,
        variable: str = 'OPENAI_API_KEY',
        persistence: CredentialPersistence = 'session',
    ) -> None:
        """Load one API key from a file into the platform's credential store.

        Use this for a local .env-style workflow. Choose the file, the variable containing
        the key, and the reference name that model settings will use. Only that variable is
        read. The file is not executed or changed, and other variables are not imported.

        After loading, the key is handled just like set_model_credential(): keep it in
        memory for this session or save it in the supported system credential store.

        Args:
            reference: Local credential reference; the reference itself is safe to save in model
                configuration.
            path: Explicit credential file path; only variable is extracted.
            variable: Exact variable name to extract from the selected credential file.
            persistence: session for Platform-owned memory; os for an available OS credential
                backend.

        Returns:
            None. The extracted key is stored under reference using the chosen persistence.

        Raises:
            ContractError: If the variable/file syntax or key is invalid, or storage is unavailable.
            OSError: If the selected file cannot be read.

        Example:
            app.load_model_credential('personal', env_path, variable='OPENAI_API_KEY')
        """
        from sim_platform.models.credential_file import read_credential

        self.set_model_credential(
            reference, read_credential(path, variable), persistence=persistence
        )

    def check_model_credential(self, reference: str) -> None:
        """Confirm that an API key is available under this reference name.

        Use this before starting an Agent that relies on a key saved earlier, for example
        one kept in the system credential store by a previous session. Nothing is stored or
        changed, and the key is not returned. A key set for this session is found first;
        otherwise the supported system credential store is asked for it, which may make the
        operating system ask the user for permission.

        Args:
            reference: Local credential reference; the reference itself is safe to save in model
                configuration.

        Returns:
            None. Returning means a usable key exists under reference.

        Raises:
            ContractError: If no key is stored under reference, the stored value is unusable,
                or the system credential store cannot be read.

        Example:
            app.check_model_credential('personal')
        """
        self._credential_store().get(reference)

    def delete_model_credential(self, reference: str) -> None:
        """Remove a stored API key using its reference name.

        Use this when the user removes a model credential from the app. It removes the local
        stored value, not the key at the model provider. A running agent may already have a
        copy; stop and close that experiment to release its process.

        System-store deletion follows the backend's missing-entry and error behavior. Asking
        for system storage when no supported backend exists raises ContractError.

        Args:
            reference: Local credential reference; the reference itself is safe to save in model
                configuration.

        Returns:
            None. Session material is removed, or deletion is delegated to the OS store.

        Raises:
            ContractError: If an identity, configuration or lifecycle precondition is invalid.

        Example:
            app.delete_model_credential('personal')
        """
        self._credential_store().delete(reference)

    def configure_experiment_model(
        self,
        experiment: AgentResearchController,
        profile: ModelProfile,
        *,
        provider: Optional[ModelProvider] = None,
    ) -> ModelProfile:
        """Set up the model and API key that a prepared agent experiment can use.

        Call this once after creating the agent experiment and before Play. experiment must
        be a ready agent controller from this Platform. profile is a ModelProfile describing
        the model; it is not the user profile ID used by account methods. Its credential_ref
        selects a key previously stored on this Platform.

        The provider setup and selected key are passed to the Agent process, where model
        requests run. The default provider follows ``profile.provider``: OpenAIResponses for
        'openai-responses', or OpenAICompatibleChat for a local 'openai-compatible' server
        (vLLM, Ollama, llama.cpp) at ``profile.base_url``, which must be on this machine or
        a private network so no data leaves the premises. A custom provider must offer
        async request(); request_received() can additionally report when the raw reply arrives.
        For integration='configured', the built-in template uses this model to run its
        prompt/tool loop after Play. Grant model.request and control.finish in the
        experiment settings; grant control.wait if waiting should be available. Custom
        trusted programs still control their own loop.

        Args:
            experiment: Ready AgentResearchController created by this Platform; bind before
                Play.
            profile: ModelProfile containing provider/model/credential_ref and network/output
                bounds; not a user profile ID.
            provider: Optional trusted async provider adapter; None selects the built-in
                adapter for ``profile.provider``.

        Returns:
            ModelProfile: The same supplied profile object. The experiment remains ready; no
            model request is sent.

        Raises:
            ContractError: If an identity, configuration or lifecycle precondition is invalid.

        Example:
            from sim_platform.models.contracts import ModelProfile
            model = ModelProfile('openai-responses', model_name, 'personal')
            app.configure_experiment_model(experiment, model)
            experiment.play()
            # Or a local server, with no key and nothing sent off this machine:
            # ModelProfile('openai-compatible', 'qwen2.5:7b',
            #              base_url='http://127.0.0.1:11434/v1')
        """
        from sim_platform.models.contracts import ModelProfile
        from sim_platform.models.providers.local import default_provider
        from sim_platform.decisions.agent.runner import AgentDecisionRunner

        if (
            experiment.app is not self._operations
            or not isinstance(profile, ModelProfile)
            or not isinstance(experiment.runner, AgentDecisionRunner)
            or experiment.runner.pipeline.snapshot()['state'] != 'ready'
        ):
            raise ContractError('configure a ready Agent experiment owned by this Platform')
        if provider is None and profile.provider == 'openai-responses':
            from sim_platform.network import require_network

            require_network('the hosted OpenAI provider')
        runner = experiment.runner
        if runner.model_configured:
            raise ContractError('model requires live mode and one model binding')
        # A local server without a key has no credential to look up.
        secret = (
            self._credential_store().get(profile.credential_ref) if profile.credential_ref else ''
        )
        runner.bind_local_model(profile, secret, provider or default_provider(profile))
        return profile
