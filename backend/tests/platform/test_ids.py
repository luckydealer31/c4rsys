import uuid

import pytest

from c4rsys.platform.ids import (
    ALPHABET,
    INITIAL_LENGTH,
    generate_public_id,
    normalize_public_id,
    uuid7,
)


def test_uuid7_has_version_and_variant() -> None:
    value = uuid7()

    assert value.version == 7
    assert value.variant == uuid.RFC_4122


def test_uuid7_is_time_ordered_across_milliseconds() -> None:
    first = uuid7()
    later = uuid.UUID(int=uuid7().int + (1 << 80))  # simulate a later millisecond

    assert first < later


def test_alphabet_excludes_ambiguous_letters() -> None:
    # ТЗ § 2.1.4: 32 symbols, latin I, L, O and U excluded.
    assert len(ALPHABET) == 32
    assert not set("ILOU") & set(ALPHABET)


@pytest.mark.parametrize(("prefix", "length"), list(INITIAL_LENGTH.items()))
def test_generated_id_has_prefix_and_initial_length(prefix: str, length: int) -> None:
    public_id = generate_public_id(prefix)

    head, body = public_id.split("-")
    assert head == prefix
    assert len(body) == length
    assert set(body) <= set(ALPHABET)


def test_unknown_prefix_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unknown"):
        generate_public_id("XYZ")


def test_length_can_grow_but_not_shrink() -> None:
    assert len(generate_public_id("USR", length=10).split("-")[1]) == 10
    with pytest.raises(ValueError, match="shorter"):
        generate_public_id("USR", length=7)


def test_normalize_accepts_lowercase_and_longer_ids() -> None:
    assert normalize_public_id(" usr-7k3m9q2x ") == "USR-7K3M9Q2X"
    assert normalize_public_id("CMP-7K3M9Q2X5R") == "CMP-7K3M9Q2X5R"


@pytest.mark.parametrize(
    "value",
    ["", "USR-7K3M9Q2", "USR-7K3M9Q2I", "ABC-7K3M9Q2X", "USR7K3M9Q2X", "VHL-7K3M9Q2X"],
)
def test_normalize_rejects_invalid_ids(value: str) -> None:
    assert normalize_public_id(value) is None
