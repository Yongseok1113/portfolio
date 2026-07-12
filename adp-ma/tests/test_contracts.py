import pandas as pd

from adp_ma.contracts import (
    RowCountRelation,
    SchemaContract,
    ValueConstraint,
    verify_contract,
)


def test_clean_pass():
    contract = SchemaContract(
        required_input_columns={"a": "int"},
        add_columns={"b": "float"},
        preserve_columns=["a"],
        row_count=RowCountRelation.EQUAL,
    )
    df_in = pd.DataFrame({"a": [1, 2]})
    df_out = pd.DataFrame({"a": [1, 2], "b": [1.0, 2.0]})
    result = verify_contract(contract, df_in, df_out)
    assert result.ok
    assert result.violations == []


def test_missing_added_column_is_critical():
    contract = SchemaContract(add_columns={"total": "float"})
    df = pd.DataFrame({"a": [1]})
    result = verify_contract(contract, df, df.copy())
    assert not result.ok
    assert any(v.column == "total" for v in result.critical)


def test_dropped_preserve_column_is_critical():
    contract = SchemaContract(preserve_columns=["keep_me"])
    df_in = pd.DataFrame({"keep_me": [1], "x": [2]})
    df_out = pd.DataFrame({"x": [2]})
    assert not verify_contract(contract, df_in, df_out).ok


def test_row_count_equal_violated():
    contract = SchemaContract(row_count=RowCountRelation.EQUAL)
    df_in = pd.DataFrame({"a": [1, 2, 3]})
    df_out = pd.DataFrame({"a": [1]})
    assert not verify_contract(contract, df_in, df_out).ok


def test_value_constraint_range_and_nulls():
    contract = SchemaContract(
        value_constraints=[
            ValueConstraint(column="amount", min_value=0, no_nulls=True)
        ]
    )
    df_out = pd.DataFrame({"amount": [10.0, -5.0, None]})
    result = verify_contract(contract, pd.DataFrame(), df_out)
    assert not result.ok
    assert len(result.critical) == 2  # 음수 + null


def test_sanitize_drops_hallucinated_input_requirements():
    from adp_ma.contracts import sanitize_contract

    contract = SchemaContract(
        required_input_columns={"df": "any", "amount": "float"},
        preserve_columns=["amount", "ghost"],
        add_columns={"total": "float"},  # 출력측 계약은 유지되어야 함
    )
    sanitize_contract(contract, ["amount", "region"])
    assert contract.required_input_columns == {"amount": "float"}
    assert contract.preserve_columns == ["amount"]
    assert contract.add_columns == {"total": "float"}


def test_dtype_mismatch_is_warning_not_critical():
    contract = SchemaContract(add_columns={"b": "int"})
    df_out = pd.DataFrame({"b": ["not-int"]})
    result = verify_contract(contract, pd.DataFrame(), df_out)
    assert result.ok  # warning뿐이면 통과
    assert len(result.violations) == 1
