"""Saved Agent definitions and per-run settings for synthetic tests."""


def definition(agent_id='demo', revision=1):
    return dict(
        agent_id=agent_id,
        revision=str(revision),
        name='Demo',
        integration='configured',
        carrier='platform',
        model_profile_ref='model-1',
        initial_prompt='Use tools to evaluate opportunities.',
        parameters={'nested': [1]},
        requires=['market.quote'],
        schedule=dict(
            mode='interval', interval_seconds='60', busy_event_policy='preserve_interval', events=[]
        ),
    )


def settings():
    return dict(
        model_profile_revision='1',
        model_parameters={},
        granted_tools=['market.quote'],
        symbol='AAPL',
        start_ns='36000000000000',
        end_ns='57600000000000',
        speed='1x',
        source_manifest_ref='source-1',
        timeout_seconds='30',
        retry=dict(
            max_attempts=3,
            format_max_attempts=3,
            base_delay_seconds='1',
            max_delay_seconds='8',
            retry_classes=['transport', 'rate_limit', 'temporary_provider', 'timeout'],
        ),
        limits=dict(
            tool_request_bytes=1048576,
            tool_response_bytes=1048576,
            pending_requests=128,
            page_items=100,
            event_buckets=16,
            depth_levels=5,
            json_depth=32,
        ),
        lag=dict(threshold_seconds='2', window_seconds='5', profile_ref='fixture-only'),
        support_profile_ref='fixture-support',
    )


def two_symbol_agent(context):
    """Reads both symbols' quotes, buys one share of the second at market, then finishes."""

    class Agent:
        def run(self, task):
            symbols = ['ACME', 'BETA']
            self.quotes = [context.tool('market.quote').run(symbol=s) for s in symbols]
            context.tool('orders.submit_market').run(symbol='BETA', side='buy', quantity_shares=1)
            context.tool('control.finish').run()

    return Agent()
