"""The fixture format and replay transport."""

import pytest

from grundfos_ble.exceptions import RequestTimeout
from grundfos_ble.genibus.telegram import Telegram
from grundfos_ble.testing import ReplayTransport, parse_fixture

from .conftest import FIXTURES, REQ_PROCESS_VALUES


def test_parse_fixture_joins_pieces_and_keeps_comments() -> None:
    text = """
    # first request
    > 27 07 e7 f8 0a 03 5d 01 22 62 7c
    < 24 05 f8 e7
    < 0a 01 00 ae a2

    > 2705e7f8038106e587
    """
    exchanges = parse_fixture(text)
    assert len(exchanges) == 2
    assert exchanges[0].request == REQ_PROCESS_VALUES
    assert exchanges[0].reply == bytes.fromhex("2405f8e70a0100aea2")
    assert exchanges[0].comment == "first request"
    assert exchanges[1].reply == b""


def test_parse_fixture_errors() -> None:
    with pytest.raises(ValueError, match="before any request"):
        parse_fixture("< 24 05")
    with pytest.raises(ValueError, match="unexpected"):
        parse_fixture("! 24 05")


async def test_replay_transport() -> None:
    transport = ReplayTransport.from_fixture(FIXTURES / "measured-2026-11-09.hex")
    await transport.connect()
    assert transport.is_connected
    reply = await transport.request(Telegram.decode(REQ_PROCESS_VALUES))
    assert reply.is_reply
    await transport.send(Telegram.decode(REQ_PROCESS_VALUES))
    assert transport.sent == [REQ_PROCESS_VALUES, REQ_PROCESS_VALUES]
    with pytest.raises(RequestTimeout):
        await transport.request(Telegram.decode(bytes.fromhex("2705e7f8038106e587")))
    await transport.disconnect()
    assert not transport.is_connected
