"""Tests for the synthetic recording generator."""

import json

from src.sample_data import RUNNERS, generate_race


def read(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_recording_is_valid_stream_messages(tmp_path):
    messages = read(generate_race(tmp_path / "race", duration_seconds=60))

    assert len(messages) == 62  # one per second, then in-play and closed
    assert all(m["op"] == "mcm" and len(m["mc"]) == 1 for m in messages)
    assert [m["pt"] for m in messages] == sorted(m["pt"] for m in messages)


def test_first_message_is_a_full_image_with_definition(tmp_path):
    first = read(generate_race(tmp_path / "race", duration_seconds=60))[0]["mc"][0]

    assert first["img"] is True
    definition = first["marketDefinition"]
    assert definition["status"] == "OPEN" and definition["inPlay"] is False
    assert [r["id"] for r in definition["runners"]] == [r[0] for r in RUNNERS]
    assert {rc["id"] for rc in first["rc"]} == {r[0] for r in RUNNERS}


def test_recording_ends_in_play_then_settled(tmp_path):
    messages = read(generate_race(tmp_path / "race", duration_seconds=60, winner=103))
    in_play = messages[-2]["mc"][0]["marketDefinition"]
    closed = messages[-1]["mc"][0]["marketDefinition"]

    assert in_play["inPlay"] is True and in_play["status"] == "OPEN"
    assert closed["status"] == "CLOSED"
    statuses = {r["id"]: r["status"] for r in closed["runners"]}
    assert statuses[103] == "WINNER"
    assert all(s == "LOSER" for sid, s in statuses.items() if sid != 103)


def last_price(messages, selection_id):
    for message in reversed(messages):
        for rc in message["mc"][0].get("rc", []):
            if rc["id"] == selection_id:
                return rc["ltp"]


def test_steamer_shortens_and_drifter_lengthens(tmp_path):
    messages = read(generate_race(tmp_path / "race"))
    assert last_price(messages, 102) < 6.0 * 0.85
    assert last_price(messages, 103) > 4.0 * 1.15


def test_reversal_ends_beyond_the_opening_price(tmp_path):
    messages = read(generate_race(tmp_path / "race", reversal=True))
    assert last_price(messages, 102) > 6.0
    assert last_price(messages, 103) < 4.0


def test_same_seed_gives_identical_output(tmp_path):
    a = generate_race(tmp_path / "a", seed=3).read_text()
    b = generate_race(tmp_path / "b", seed=3).read_text()
    c = generate_race(tmp_path / "c", seed=4).read_text()
    assert a == b
    assert a != c
