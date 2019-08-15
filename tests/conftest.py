# Copyright (C) DATADVANCE, 2010-2020
#
# Permission is hereby granted, free of charge, to any person obtaining
# a copy of this software and associated documentation files (the
# "Software"), to deal in the Software without restriction, including
# without limitation the rights to use, copy, modify, merge, publish,
# distribute, sublicense, and/or sell copies of the Software, and to
# permit persons to whom the Software is furnished to do so, subject to
# the following conditions:
#
# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
# IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY
# CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT,
# TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE
# SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

"""Auxiliary fixtures to simplify testing."""

import threading

import channels
import django
import graphene
import pytest

import channels_graphql_ws
import channels_graphql_ws.testing


@pytest.fixture
def gql(db, request):
    """PyTest fixture for testing GraphQL WebSocket backends.

    The fixture provides a method to setup GraphQL testing backend for
    the given GraphQL schema (query, mutation, and subscription). In
    particular: it sets up an instance of `GraphqlWsConsumer` and an
    instance of `GraphqlWsClient`. The former one is returned
    from the function.

    Syntax:
        gql(
            *,
            query=None,
            mutation=None,
            subscription=None,
            consumer_attrs=None,
            communicator_kwds=None
        ):

    Args:
        query: Root GraphQL query. Optional.
        mutation: Root GraphQL subscription. Optional.
        subscription: Root GraphQL mutation. Optional.
        consumer_attrs: `GraphqlWsConsumer` attributes dict. Optional.
        communicator_kwds: Extra keyword arguments for the Channels
            `channels.testing.WebsocketCommunicator`. Optional.

    Returns:
        An instance of the `GraphqlWsClient` class which has many
        useful GraphQL-related methods, see the `GraphqlWsClient`
        class docstrings for details.

    Use like this:
    ```
    def test_something(gql):
        client = gql(
            # GraphQl schema.
            query=MyQuery,
            mutation=MyMutation,
            subscription=MySubscription,
            # `GraphqlWsConsumer` settings.
            consumer_attrs={"strict_ordering": True},
            # `channels.testing.WebsocketCommunicator` settings.
            communicator_kwds={"headers": [...]}
        )
        ...
    ```

    """
    # NOTE: We need Django DB to be initialized each time we work with
    # `GraphqlWsConsumer`, because it uses threads and sometimes calls
    # `django.db.close_old_connections()`.
    del db

    issued_clients = []

    def client_constructor(
        *,
        query=None,
        mutation=None,
        subscription=None,
        consumer_attrs=None,
        communicator_kwds=None,
    ):
        """Setup GraphQL consumer and the communicator for tests."""

        class ChannelsConsumer(channels_graphql_ws.GraphqlWsConsumer):
            """Channels WebSocket consumer for GraphQL API."""

            schema = graphene.Schema(
                query=query,
                mutation=mutation,
                subscription=subscription,
                auto_camelcase=False,
            )

        # Set additional attributes to the `ChannelsConsumer`.
        if consumer_attrs is not None:
            for attr, val in consumer_attrs.items():
                setattr(ChannelsConsumer, attr, val)

        application = channels.routing.ProtocolTypeRouter(
            {
                "websocket": channels.routing.URLRouter(
                    [django.urls.path("graphql/", ChannelsConsumer)]
                )
            }
        )

        transport = channels_graphql_ws.testing.GraphqlWsTransport(
            application=application,
            path="graphql/",
            communicator_kwds=communicator_kwds,
        )

        client = channels_graphql_ws.testing.GraphqlWsClient(transport)
        issued_clients.append(client)
        return client

    yield client_constructor

    # Assert all issued client are properly finalized.
    for client in reversed(issued_clients):
        assert (
            not client.connected
        ), f"Test has left connected client: {request.node.nodeid}!"


@pytest.fixture(scope="session", autouse=True)
def synchronize_inmemory_channel_layer():
    """Monkeypatch `InMemoryChannelLayer` to make it thread safe.

    Without this we have a blinking fails in the unit tests run:
    Traceback (most recent call last):
        File ".../site-packages/promise/promise.py", line 842, in handle_future_result
            resolve(future.result())
        File ".../tests/test_concurrent.py", line 994, in mutate
            await OnChatMessageSentAsync.notify(message=message, user_id=user_id)
        File ".../tests/test_concurrent.py", line 940, in notify
            await super().broadcast(group=group, payload=message)
        File ".../channels_graphql_ws/graphql_ws.py", line 248, in broadcast_async
            "payload": serialized_payload,
        File ".../site-packages/channels/layers.py", line 351, in group_send
            for channel in self.groups.get(group, set()):
     graphql.error.located_error.GraphQLLocatedError:
         dictionary changed size during iteration
    """
    mutex = threading.RLock()

    def wrap(func):
        async def wrapper(*args, **kwds):
            with mutex:
                return await func(*args, **kwds)

        return wrapper

    public_callable_attr_names = (
        attr_name
        for attr_name in dir(channels.layers.InMemoryChannelLayer)
        if callable(getattr(channels.layers.InMemoryChannelLayer, attr_name))
        and not attr_name.startswith("_")
    )
    for attr_name in public_callable_attr_names:
        setattr(
            channels.layers.InMemoryChannelLayer,
            attr_name,
            wrap(getattr(channels.layers.InMemoryChannelLayer, attr_name)),
        )
