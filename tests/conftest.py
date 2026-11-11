"""Fixtures partagées : serveur S3 local et bucket de test isolé."""
import os
import uuid

import pytest

from storage import s3_store
from storage.s3_store import S3Error


@pytest.fixture(scope="session")
def s3_client():
    """Client S3 vers RustFS ; démarre le serveur si besoin (skip en local s'il est indisponible)."""
    try:
        return s3_store.start_store()
    except S3Error as e:
        if os.environ.get("CI"):
            raise  # en CI, l'absence de S3 est une vraie erreur
        pytest.skip(f"S3 local indisponible : {e}")


@pytest.fixture
def bucket(s3_client):
    """Bucket unique par test, vidé et supprimé à la fin."""
    nom = f"test-{uuid.uuid4().hex[:12]}"
    s3_store.create_bucket(s3_client, nom)
    yield nom
    for cle in s3_store.list_keys(s3_client, nom):
        s3_client.delete_object(Bucket=nom, Key=cle)
    s3_client.delete_bucket(Bucket=nom)
