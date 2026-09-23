"""The S3 store must satisfy everything the job store and object endpoint call on it.
A missing method here once shipped in an image and hung every analysis job."""

import inspect

from ivaas.adapters.storage.objects import LocalObjectStore, S3ObjectStore

REQUIRED = {"put", "get_url", "download_to", "delete", "open", "read", "list_keys"}


def test_both_stores_expose_the_same_surface():
    for cls in (S3ObjectStore, LocalObjectStore):
        methods = {
            n for n, m in inspect.getmembers(cls, inspect.isfunction) if not n.startswith("_")
        }
        missing = REQUIRED - methods
        assert not missing, f"{cls.__name__} is missing {missing}"


def test_s3_urls_go_through_the_api():
    import asyncio

    store = S3ObjectStore("http://minio:9000", "k", "s", "b")
    assert asyncio.run(store.get_url("frames/x.jpg")) == "/api/v1/objects/frames/x.jpg"
