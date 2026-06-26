"""Shared pytest fixtures."""

import boto3
import pytest
from moto import mock_aws


@pytest.fixture
def aws_credentials(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SECURITY_TOKEN", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")


@pytest.fixture
def aws(aws_credentials):
    with mock_aws():
        yield


@pytest.fixture
def sandbox_env(monkeypatch):
    monkeypatch.setenv("SANDBOX_BUCKET", "test-bucket")
    monkeypatch.setenv("SANDBOX_DATABASE", "test_sandbox_db")
    monkeypatch.setenv("SANDBOX_ATHENA_OUTPUT", "s3://test-bucket/athena-output/")
    monkeypatch.delenv("SANDBOX_DRY_RUN", raising=False)
    monkeypatch.delenv("SANDBOX_RUN_DATE", raising=False)


@pytest.fixture
def sandbox_aws(aws, sandbox_env):
    """Full AWS + sandbox env with bucket and Glue DB pre-created."""
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket="test-bucket")
    glue = boto3.client("glue", region_name="us-east-1")
    glue.create_database(DatabaseInput={"Name": "test_sandbox_db"})
    yield
