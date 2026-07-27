from regin.schemas import Outcome, RunRecord, Usage, config_hash


def test_usage_adds_every_token_class() -> None:
    first = Usage(input_tokens=10, cache_read_tokens=5)
    total = first + Usage(output_tokens=3, cache_write_tokens=1)
    assert total.input_tokens == 10
    assert total.output_tokens == 3
    assert total.cache_read_tokens == 5
    assert total.cache_write_tokens == 1


def test_config_hash_is_stable_regardless_of_key_order() -> None:
    assert config_hash({"a": 1, "b": 2}) == config_hash({"b": 2, "a": 1})


def test_config_hash_changes_when_a_value_changes() -> None:
    assert config_hash({"model": "sonnet"}) != config_hash({"model": "opus"})


def test_run_record_serialises_to_one_jsonl_line() -> None:
    line = RunRecord(run_id="abc", task_id="t1", outcome=Outcome.RESOLVED).to_jsonl()
    assert line.endswith("\n")
    assert line.count("\n") == 1
