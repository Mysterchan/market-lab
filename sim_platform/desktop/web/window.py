"""HTML Desktop composition; each running experiment owns a worker and runtime.

The GUI owns windows and file pickers. Workers alone own Platform resources and
financial state. The management worker reads catalogues and saved results; it
never replaces another experiment's controller when the user changes tabs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set, Tuple, cast
from uuid import uuid4

from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import (
    QMainWindow,
    QFileDialog,
    QMessageBox,
    QStackedWidget,
    QWidget,
    QVBoxLayout,
    QLabel,
    QPushButton,
)

from sim_platform.desktop.presentation import (
    describe_datasets,
    describe_events,
    money,
    performance,
    portfolio_values,
)
from .frontend_bridge import FrontendView
from .workers import WorkerOwner

if TYPE_CHECKING:  # Annotation only.
    from PySide6.QtGui import QCloseEvent

# Initial and minimum window size.
INITIAL_SIZE = (1360, 900)
MINIMUM_SIZE = (1000, 700)
# Margin around the display-failure page.
FAILURE_PAGE_MARGIN = 40
# Longest credential reference.
REFERENCE_MAX_LENGTH = 128

# A detached session view, and command arguments from the page.
View = Dict[str, Any]
Arguments = Dict[str, Any]


def several_symbols(args: Arguments) -> None:
    """Check and normalise a setup on several symbols; its first pair is path and symbol."""
    symbols, paths = args['symbols'], args.get('paths') or []
    if len(symbols) < 2 or len(paths) not in (1, len(symbols)):
        raise ValueError('Choose two or more symbols, each with market data')
    if any(not str(s).strip() for s in symbols) or any(not str(p).strip() for p in paths):
        raise ValueError('Choose market data and a symbol on every row')
    if args.get('policy') not in ('human', 'model'):
        raise ValueError('Several symbols work with Manual trading or a Model file')
    if args.get('execution_mode') != 'controlled':
        raise ValueError('Several symbols run in Controlled playback')
    args['symbols'] = [str(s).strip().upper() for s in symbols]
    args['paths'] = [str(p) for p in paths]
    args['symbol'], args['path'] = args['symbols'][0], args['paths'][0]


class WebResearchWindow(QMainWindow):
    """Present real platform events through the accepted Desktop design.

    app.open_window creates one per application. It owns one WorkerOwner per open
    experiment plus the 'manager' worker; closing it stops all of them first.
    """

    def __init__(
        self,
        directory: Any,
        library: Optional[Any] = None,
        demo_source: Optional[Tuple[Any, Any]] = None,
    ) -> None:
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle('Market Lab')
        self.resize(*INITIAL_SIZE)
        self.setMinimumSize(*MINIMUM_SIZE)
        self.directory, self.library = directory, library
        self.owners: Dict[str, WorkerOwner] = {}
        self.views: Dict[str, View] = {}
        self.session_names: Dict[str, str] = {}
        self.session_profiles: Dict[str, str] = {}
        self.catalogue: Dict[str, Any] = {}
        self.dataset_catalogue: Dict[str, Any] = {}
        self.agent_catalogue: Dict[str, Any] = {}
        self.preparing: Set[str] = set()
        self.busy: Set[str] = set()
        self._reset_pending = False
        self._session_credentials: Dict[str, str] = {}
        self._os_credentials: Set[str] = set()
        self._closing = False
        self.ui_state: Dict[str, Any] = {}
        self.browser = FrontendView(self)
        self.pages = QStackedWidget(self)
        self.pages.addWidget(self.browser)
        self.failure_page = QWidget(self)
        failure_layout = QVBoxLayout(self.failure_page)
        failure_layout.setContentsMargins(
            FAILURE_PAGE_MARGIN, FAILURE_PAGE_MARGIN, FAILURE_PAGE_MARGIN, FAILURE_PAGE_MARGIN
        )
        failure_layout.addWidget(QLabel('The workspace display could not be loaded.'))
        failure_layout.addWidget(
            QLabel('Your experiments remain in the local engine. Reload the display to reconnect.')
        )
        retry = QPushButton('Reload workspace')
        retry.clicked.connect(self._reload_frontend)
        failure_layout.addWidget(retry)
        failure_layout.addStretch()
        self.pages.addWidget(self.failure_page)
        self.setCentralWidget(self.pages)
        self.browser.loadFinished.connect(self._loaded)
        self.browser.page().renderProcessTerminated.connect(self._renderer_failed)
        self.browser.commands.requested.connect(self.dispatch)
        from .menus import DesktopMenus

        self.menus = DesktopMenus(self)
        self.manager = self._owner('manager', demo_source)
        self.manager.start()

    def publish(self, kind: str, value: Any) -> None:
        self.browser.commands.publish(kind, value)

    def _owner(self, identity: str, demo_source: Optional[Tuple[Any, Any]] = None) -> WorkerOwner:
        owner = WorkerOwner(identity, self.directory, self.library, demo_source, self)
        self.owners[identity] = owner
        owner.event.connect(self._event)
        owner.closed.connect(self._closed)
        return owner

    @Slot(str, object)
    def _event(self, kind: str, value: Any) -> None:
        """Receive worker results on the GUI thread before updating presentation state."""
        if kind == 'view':
            self._view(value['session'], value['value'])
        elif kind == 'catalog':
            if value['session'] == 'manager':
                self._catalog(value['value'])
        elif kind == 'datasets':
            self._datasets(value)
        elif kind == 'agents':
            self._agents(value)
        elif kind == 'reset':
            self._reset_complete(value)
        elif kind == 'task':
            identity = value['session']
            if identity in self.preparing and value.get('state') in ('failed', 'cancelled'):
                self.preparing.discard(identity)
                if not self.views.get(identity, {}).get('run'):
                    self.owners[identity].stop()
            self.publish(kind, value)
        elif kind == 'busy':
            if value['busy']:
                self.busy.add(value['session'])
            else:
                self.busy.discard(value['session'])
            self.publish(kind, value)
        elif kind == 'error':
            self._error(value['session'], value['message'])
        else:
            self.publish(kind, value)

    @Slot(object)
    def _catalog(self, value: Dict[str, Any]) -> None:
        changed = self.catalogue.get('profile') != value.get('profile')
        if changed:
            self.agent_catalogue = {}
        self.catalogue = value
        self.publish('catalog', value)
        if changed and value.get('profile'):
            self._send('manager', 'agents', dict(profile=value['profile']))

    @Slot(object)
    def _datasets(self, value: Dict[str, Any]) -> None:
        self.dataset_catalogue = describe_datasets(value)
        self.publish('datasets', self.dataset_catalogue)

    @Slot(object)
    def _agents(self, value: Dict[str, Any]) -> None:
        self.agent_catalogue = value
        self.publish('agents', value)

    def _view(self, identity: str, value: View) -> None:
        self.views[identity] = value
        self.menus.update(self.ui_state)
        self.publish(
            'view',
            dict(
                session=identity,
                profile=self.session_profiles.get(identity, self.catalogue.get('profile')),
                view=describe_events(value),
                portfolio=portfolio_values(value),
                performance=performance(value) if value.get('run') else None,
                name=self.session_names.get(identity),
            ),
        )
        if identity in self.preparing and value.get('run'):
            self.preparing.discard(identity)
            self.publish('prepared', dict(session=identity))

    def _error(self, identity: str, message: str) -> None:
        preparing = identity in self.preparing
        self.preparing.discard(identity)
        if preparing and not self.views.get(identity, {}).get('run'):
            self.owners[identity].stop()
        self.publish('error', dict(session=identity, message=message))

    def _send(self, identity: str, command: str, arguments: Optional[Arguments] = None) -> None:
        if identity not in self.owners:
            raise ValueError('Experiment is no longer open')
        self.owners[identity].command.emit(command, arguments or {})

    def _refresh(self) -> None:
        self.publish('catalog', self.catalogue)
        self.publish('datasets', self.dataset_catalogue)
        self.publish('agents', self.agent_catalogue)
        for identity, value in self.views.items():
            self._view(identity, value)
        self._send('manager', 'catalog', dict(profile=self.catalogue.get('profile')))
        self._send('manager', 'datasets')

    def _prepare(self, arguments: Arguments) -> None:
        """Validate the new-experiment form and start a worker to prepare it."""
        if self.preparing:
            raise ValueError('Finish or cancel preparation before creating another experiment')
        identity = uuid4().hex
        args = dict(arguments)
        if args.get('policy') not in ('human', 'rule', 'hold', 'model', 'agent'):
            raise ValueError('Choose a valid decision maker')
        if args.get('policy') == 'model' and not str(args.get('model_file') or '').strip():
            raise ValueError('Choose the model file')
        if args.get('execution_mode') not in ('live', 'controlled'):
            raise ValueError('Choose a valid playback mode')
        if args.get('symbols'):
            several_symbols(args)
        if not str(args.get('path', '')).strip() or not str(args.get('symbol', '')).strip():
            raise ValueError('Choose market data and a symbol')
        args['profile'] = self.catalogue['profile']
        args['fee_per_share'] = money(str(args.get('fee_per_share', '0.01')))
        # Accounts may be reused between experiments, but never by concurrent runs.
        selected = args.get('account')
        account = next((a for a in self.catalogue.get('accounts', []) if a['id'] == selected), None)
        if selected and account is None:
            raise ValueError('Choose an account belonging to this researcher')
        if selected and any(
            (v.get('run') or {}).get('account_id') == selected
            and (v.get('execution') or {}).get('status') == 'running'
            for v in self.views.values()
        ):
            raise ValueError('This account is used by an open experiment; choose another account')
        account_name = args.pop('account_name', '')
        if selected:
            # A selected account was found in the catalogue above.
            account_name = cast(Dict[str, Any], account)['name']
        cash = money(str(args.pop('cash', '0')))
        if not selected and (cash <= 0 or not account_name.strip()):
            raise ValueError('Enter an account name and positive starting cash')
        self.session_names[identity] = account_name
        self.session_profiles[identity] = args['profile']
        owner = self._owner(identity)
        self.preparing.add(identity)
        pending: Dict[str, Any] = {'submitted': False}

        def initialized(catalogue: Dict[str, Any]) -> None:
            if pending['submitted']:
                return
            for reference, secret in self._session_credentials.items():
                owner.command.emit(
                    'save_credential',
                    dict(reference=reference, secret=secret, persistence='session'),
                )
            for reference in self._os_credentials - self._session_credentials.keys():
                owner.command.emit('load_credential', dict(reference=reference))
            owner.catalogued.disconnect(initialized)
            pending['submitted'] = True
            pending['accounts'] = {account['id'] for account in catalogue['accounts']}
            if selected:
                pending['prepared'] = True
                owner.catalogued.disconnect(account_created)
                owner.command.emit('prepare_experiment', args)
                return
            owner.command.emit(
                'create_account', dict(profile=args['profile'], name=account_name, cash=cash)
            )

        def account_created(catalogue: Dict[str, Any]) -> None:
            if not pending['submitted'] or pending.get('prepared'):
                return
            accounts = [
                a
                for a in catalogue['accounts']
                if a['name'] == account_name and a['id'] not in pending['accounts']
            ]
            if not accounts:
                return
            pending['prepared'] = True
            owner.catalogued.disconnect(account_created)
            owner.command.emit('prepare_experiment', dict(args, account=accounts[-1]['id']))

        owner.catalogued.connect(initialized)
        owner.catalogued.connect(account_created)
        owner.start()
        self.publish('preparing', dict(session=identity))

    @Slot(str, object)
    def dispatch(self, name: str, arguments: Arguments) -> None:
        """Translate bounded web commands without moving I/O onto the GUI thread.

        Each command has one `_command_<name>` handler below. Unknown names are ignored;
        the bridge already restricts which names the page may send. Invalid input is
        reported to the page as an error notice, never raised into the Qt event loop.
        """
        if self._closing:
            return
        handler = getattr(self, '_command_' + name, None)
        if handler is None:
            return
        try:
            handler(dict(arguments))
        except (ValueError, KeyError, TypeError) as exc:
            self.publish('error', dict(message=str(exc)))

    # Manager commands that pass their arguments through unchanged.
    def _to_manager(self, name: str, args: Arguments) -> None:
        self._send('manager', name, args)

    # Manager commands that act on the selected researcher profile.
    def _to_manager_profile(self, name: str, args: Arguments) -> None:
        self._send('manager', name, dict(args, profile=self.catalogue.get('profile')))

    def _running_sessions(self) -> List[View]:
        """Views of open experiments whose financial execution has not ended."""
        return [
            view
            for identity, view in self.views.items()
            if identity != 'manager' and (view.get('execution') or {}).get('status') == 'running'
        ]

    def _command_ui_state(self, args: Arguments) -> None:
        self.menus.update(args)

    def _command_refresh(self, args: Arguments) -> None:
        self._refresh()

    def _command_load_market(self, args: Arguments) -> None:
        self._send('manager', 'load', args)

    def _command_prepare(self, args: Arguments) -> None:
        self._prepare(args)

    def _command_session(self, args: Arguments) -> None:
        identity, method = args.pop('session'), args.pop('method')
        if method not in ('play', 'pause', 'step', 'stop', 'order', 'cancel', 'handoff'):
            raise ValueError('Unknown experiment operation')
        if method == 'order' and args.get('order_type', 'limit') == 'limit':
            args['limit_price'] = money(str(args['limit_price']))
        elif method == 'order':
            args.pop('limit_price', None)
        self._send(identity, method, args)

    def _command_reset_local_data(self, args: Arguments) -> None:
        self._request_reset()

    def _command_dataset_storage(self, args: Arguments) -> None:
        path = QFileDialog.getExistingDirectory(self, 'Choose dataset storage')
        if path:
            self._to_manager('dataset_storage', dict(folder=path))

    def _command_recover_stop(self, args: Arguments) -> None:
        if any(
            (view.get('run') or {}).get('id') == args['run_id'] for view in self._running_sessions()
        ):
            raise ValueError('This experiment is still open; end it from its workspace')
        self._to_manager_profile('recover_stop', dict(run_id=args['run_id']))

    def _command_open_saved(self, args: Arguments) -> None:
        self._to_manager_profile('view_run', dict(run_id=args['run_id']))

    def _command_pick_file(self, args: Arguments) -> None:
        target = args.get('target', 'recording')
        if target in ('agent-folder', 'wheelhouse'):
            path = QFileDialog.getExistingDirectory(self, 'Choose Agent folder')
            if target == 'agent-folder':
                target = 'agent'
        else:
            path, _ = QFileDialog.getOpenFileName(self, 'Choose local file')
        if path:
            self.publish('file', dict(target=target, path=path))

    def _command_locate_dataset(self, args: Arguments) -> None:
        path, _ = QFileDialog.getOpenFileName(self, 'Locate original recording')
        if path:
            self._to_manager('locate_dataset', dict(identity=args['identity'], path=path))

    def _command_export_comparison(self, args: Arguments) -> None:
        path, _ = QFileDialog.getSaveFileName(self, 'Export comparison', 'comparison.json')
        if path:
            self._to_manager('export_comparison', dict(path=path))

    def _command_export_sdk(self, args: Arguments) -> None:
        from sim_platform.sdk.distribution import FILENAME

        path, _ = QFileDialog.getSaveFileName(self, 'Export Agent SDK', FILENAME)
        if path:
            self._to_manager('export_sdk', dict(path=path))

    def _command_export(self, args: Arguments) -> None:
        path, _ = QFileDialog.getSaveFileName(self, 'Export experiment', 'experiment.json')
        if path:
            self._send(args.get('session', 'manager'), 'export', dict(path=path))

    def _command_select_profile(self, args: Arguments) -> None:
        if self.preparing or self._running_sessions():
            raise ValueError('End open experiments before changing researcher')
        self._to_manager('catalog', dict(profile=args['profile']))

    def _command_cancel_task(self, args: Arguments) -> None:
        self.manager.cancel.emit()

    def _command_compare(self, args: Arguments) -> None:
        args['cash'] = money(str(args['cash']))
        self._to_manager('compare', args)

    def _command_cancel_preparation(self, args: Arguments) -> None:
        for identity in tuple(self.preparing):
            self.owners[identity].cancel.emit()

    def _command_create_account(self, args: Arguments) -> None:
        args['cash'] = money(str(args['cash']))
        self._to_manager_profile('create_account', args)

    def _command_load_credential(self, args: Arguments) -> None:
        reference = args['reference']
        self._os_credentials.add(reference)
        self._session_credentials.pop(reference, None)
        for identity in self.owners:
            self._send(identity, 'load_credential', dict(reference=reference))

    def _command_save_credential(self, args: Arguments) -> None:
        """Keep the secret in memory for running workers; persist only when asked."""
        from sim_platform.models.credentials import validate_secret
        from sim_platform.domain.models import label

        reference, secret = args['reference'], validate_secret(args['secret'])
        label(reference, 'credential reference', REFERENCE_MAX_LENGTH)
        self._session_credentials[reference] = secret
        for identity in self.owners:
            if self.owners[identity].thread.isRunning():
                self._send(identity, 'save_credential', dict(args, persistence='session'))
        if args.get('persistence') == 'os':
            self._os_credentials.add(reference)
            self._to_manager('save_credential', args)

    def _command_add_dataset(self, args: Arguments) -> None:
        self._to_manager('add_dataset', args)

    def _command_verify_run(self, args: Arguments) -> None:
        self._to_manager('verify_run', dict(run_id=str(args['run_id'])))

    def _command_create_profile(self, args: Arguments) -> None:
        self._to_manager('create_profile', args)

    def _command_agents(self, args: Arguments) -> None:
        self._to_manager_profile('agents', args)

    def _command_save_agent(self, args: Arguments) -> None:
        self._to_manager_profile('save_agent', args)

    def _command_prepare_environment(self, args: Arguments) -> None:
        self._to_manager_profile('prepare_environment', args)

    def _command_prepare_replay(self, args: Arguments) -> None:
        self._to_manager_profile('prepare_replay', args)

    def _command_evaluation_runs(self, args: Arguments) -> None:
        self._to_manager_profile('evaluation_runs', {})

    def _command_evaluate(self, args: Arguments) -> None:
        entries = args.get('entries')
        if not isinstance(entries, dict) or not entries:
            return
        self._to_manager_profile(
            'evaluate',
            dict(
                entries={str(k): [str(r) for r in v] for k, v in entries.items()},
                baseline=args.get('baseline') or None,
                metric=str(args.get('metric') or 'total_return'),
                training_cutoffs={
                    str(k): str(v) for k, v in (args.get('training_cutoffs') or {}).items() if v
                },
                token_prices={
                    key: float(value)
                    for key, value in (args.get('token_prices') or {}).items()
                    if key in ('input', 'output') and value not in (None, '')
                }
                or None,
            ),
        )

    def _command_export_evaluation(self, args: Arguments) -> None:
        path, _ = QFileDialog.getSaveFileName(self, 'Export evaluation', 'evaluation.json')
        if path:
            self._to_manager('export_evaluation', dict(path=path))

    def confirm_close(self) -> bool:
        """Explain what Quit does before ending active work or dropping a draft."""
        active = any(
            (v.get('execution') or {}).get('status') == 'running'
            for key, v in self.views.items()
            if key != 'manager'
        )
        if not (
            active
            or self.preparing
            or self.busy
            or self.ui_state.get('dirty')
            or any(
                (v.get('preparation') or {}).get('status') == 'preparing'
                for v in self.views.values()
            )
        ):
            return True
        answer = QMessageBox.question(
            self,
            'Quit Market Lab?',
            'Active experiments will end and their results will be saved. '
            'Background work will be cancelled. Unsaved form changes will be discarded.',
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
            QMessageBox.StandardButton.Cancel,
        )
        return answer == QMessageBox.StandardButton.Yes

    def closeEvent(self, event: QCloseEvent) -> None:
        if any(owner.thread.isRunning() for owner in self.owners.values()):
            event.ignore()
            if not self._closing:
                if not self.confirm_close():
                    return
                self._closing = True
                self._session_credentials.clear()
                self._os_credentials.clear()
                self.setEnabled(False)
                for owner in self.owners.values():
                    owner.stop()
        else:
            event.accept()

    @Slot(bool)
    def _loaded(self, success: bool) -> None:
        self.pages.setCurrentWidget(self.browser if success else self.failure_page)

    def _renderer_failed(self, *_: object) -> None:
        self.pages.setCurrentWidget(self.failure_page)

    def _reload_frontend(self) -> None:
        self.pages.setCurrentWidget(self.browser)
        self.browser.reload()

    def _request_reset(self) -> None:
        if (
            self.preparing
            or self.busy
            or any(
                (v.get('preparation') or {}).get('status') == 'preparing'
                for v in self.views.values()
            )
            or any(
                (v.get('execution') or {}).get('status') == 'running' for v in self.views.values()
            )
        ):
            raise ValueError(
                'End experiments and finish or cancel background work before resetting'
            )
        if (
            QMessageBox.warning(
                self,
                'Reset local workspace?',
                'This deletes local researchers, accounts, experiments, Agent definitions and dataset registrations. '
                'Original recordings are kept. There is no automatic backup. Continue?',
                QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
                QMessageBox.StandardButton.Cancel,
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        self._reset_pending = True
        for key, owner in self.owners.items():
            if key != 'manager' and owner.thread.isRunning():
                owner.stop()
        self._closed()

    def _reset_complete(self, value: Dict[str, Any]) -> None:
        for key in list(self.owners):
            if key != 'manager':
                self.owners.pop(key).deleteLater()
        self.views.clear()
        self.session_names.clear()
        self.session_profiles.clear()
        self.preparing.clear()
        self._session_credentials.clear()
        self._os_credentials.clear()
        self.catalogue = value['catalog']
        self.dataset_catalogue = describe_datasets(value['datasets'])
        value = dict(value, datasets=self.dataset_catalogue)
        self.agent_catalogue = {}
        self.views['manager'] = value['view']
        self.publish('reset', value)
        self.menus.update({})

    @Slot()
    def _closed(self) -> None:
        if self._reset_pending and all(
            not o.thread.isRunning() for key, o in self.owners.items() if key != 'manager'
        ):
            self._reset_pending = False
            self._send('manager', 'reset_local_data')
        if self._closing and not any(o.thread.isRunning() for o in self.owners.values()):
            self.close()
