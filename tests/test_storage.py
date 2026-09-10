from pathlib import Path

import pytest

from app.storage import Run, RunStore


async def _make_store(tmp_path: Path) -> RunStore:
    store = RunStore(tmp_path / "runs.db")
    await store.init()
    return store


async def test_create_and_get_run(tmp_path):
    store = await _make_store(tmp_path)
    run_id = await store.create(
        instance_root="95", sub_instance="26434", phone="549261",
        event_type="transcript_audio",
    )

    got = await store.get(run_id)

    assert got is not None
    assert got.id == run_id
    assert got.instance_root == "95"
    assert got.sub_instance == "26434"
    assert got.phone == "549261"
    assert got.event_type == "transcript_audio"
    assert got.status == "queued"
    assert got.created_at is not None


async def test_mark_processing_updates_status(tmp_path):
    store = await _make_store(tmp_path)
    run_id = await store.create(instance_root="95", sub_instance="95", phone="1",
                                event_type="transcript_audio")

    await store.mark_processing(run_id)
    got = await store.get(run_id)

    assert got.status == "processing"
    assert got.updated_at is not None


async def test_mark_completed_persists_result_fields(tmp_path):
    store = await _make_store(tmp_path)
    run_id = await store.create(instance_root="95", sub_instance="95", phone="1",
                                event_type="transcript_audio")

    await store.mark_completed(
        run_id,
        transcription_text="hola",
        transcription_cost_usd=0.0005,
        transcription_duration_seconds=3.2,
        contact_name="Ale",
        mass_id_original="uuid-xyz",
    )
    got = await store.get(run_id)

    assert got.status == "completed"
    assert got.transcription_text == "hola"
    assert got.transcription_cost_usd == 0.0005
    assert got.transcription_duration_seconds == 3.2
    assert got.contact_name == "Ale"
    assert got.mass_id_original == "uuid-xyz"


async def test_mark_failed_persists_error(tmp_path):
    store = await _make_store(tmp_path)
    run_id = await store.create(instance_root="95", sub_instance="95", phone="1",
                                event_type="transcript_audio")

    await store.mark_failed(run_id, error_message="openrouter 500")
    got = await store.get(run_id)

    assert got.status == "failed"
    assert got.error_message == "openrouter 500"


async def test_mark_skipped_persists_reason(tmp_path):
    store = await _make_store(tmp_path)
    run_id = await store.create(instance_root="95", sub_instance="95", phone="1",
                                event_type="transcript_audio")

    await store.mark_skipped(run_id, reason="no audio attachment")
    got = await store.get(run_id)

    assert got.status == "skipped"
    assert got.error_message == "no audio attachment"


async def test_set_attachment_records_kind_and_url(tmp_path):
    store = await _make_store(tmp_path)
    run_id = await store.create(instance_root="95", sub_instance="95", phone="1",
                                event_type="transcript_audio")

    await store.set_attachment(run_id, kind="audio", url="https://hub/audio/1")
    got = await store.get(run_id)

    assert got.attachment_kind == "audio"
    assert got.attachment_url == "https://hub/audio/1"


async def test_list_returns_most_recent_first(tmp_path):
    store = await _make_store(tmp_path)
    a = await store.create(instance_root="95", sub_instance="95", phone="1",
                          event_type="transcript_audio")
    b = await store.create(instance_root="95", sub_instance="95", phone="2",
                          event_type="transcript_audio")
    c = await store.create(instance_root="95", sub_instance="95", phone="3",
                          event_type="transcript_audio")

    rows = await store.list(limit=10)

    assert [r.id for r in rows] == [c, b, a]


async def test_list_supports_pagination(tmp_path):
    store = await _make_store(tmp_path)
    ids = []
    for i in range(5):
        ids.append(await store.create(
            instance_root="95", sub_instance="95", phone=str(i),
            event_type="transcript_audio",
        ))

    page1 = await store.list(limit=2, offset=0)
    page2 = await store.list(limit=2, offset=2)

    assert [r.id for r in page1] == [ids[4], ids[3]]
    assert [r.id for r in page2] == [ids[2], ids[1]]


async def test_list_filters_by_status(tmp_path):
    store = await _make_store(tmp_path)
    a = await store.create(instance_root="95", sub_instance="95", phone="1",
                          event_type="transcript_audio")
    b = await store.create(instance_root="95", sub_instance="95", phone="2",
                          event_type="transcript_audio")
    await store.mark_completed(a, transcription_text="hi")

    completed = await store.list(status="completed")
    queued = await store.list(status="queued")

    assert [r.id for r in completed] == [a]
    assert [r.id for r in queued] == [b]


async def test_metrics_summary_over_all_runs(tmp_path):
    store = await _make_store(tmp_path)
    a = await store.create(instance_root="95", sub_instance="95", phone="1",
                          event_type="transcript_audio")
    b = await store.create(instance_root="95", sub_instance="95", phone="2",
                          event_type="transcript_audio")
    c = await store.create(instance_root="95", sub_instance="95", phone="3",
                          event_type="transcript_audio")
    await store.mark_completed(a, transcription_text="hola", transcription_cost_usd=0.001,
                              transcription_duration_seconds=3.0)
    await store.mark_completed(b, transcription_text="que tal", transcription_cost_usd=0.002,
                              transcription_duration_seconds=5.0)
    await store.mark_failed(c, error_message="boom")

    m = await store.metrics()

    assert m["total"] == 3
    assert m["completed"] == 2
    assert m["failed"] == 1
    assert m["queued"] == 0
    assert m["total_cost_usd"] == pytest.approx(0.003)
    assert m["total_duration_seconds"] == pytest.approx(8.0)


async def test_run_not_found_returns_none(tmp_path):
    store = await _make_store(tmp_path)
    got = await store.get("nonexistent")
    assert got is None
