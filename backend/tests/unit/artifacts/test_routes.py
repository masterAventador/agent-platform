from agent_platform.api.routes.artifacts import content_disposition


def test_content_disposition_encodes_unicode_and_strips_header_metacharacters() -> None:
    header = content_disposition("attachment", '报告 "final".pdf')

    assert header.startswith('attachment; filename="final.pdf";')
    assert "filename*=UTF-8''%E6%8A%A5%E5%91%8A%20%22final%22.pdf" in header
    assert "\r" not in header
    assert "\n" not in header


def test_content_disposition_uses_a_stable_ascii_fallback() -> None:
    assert content_disposition("inline", "报告.pdf").startswith('inline; filename="download.pdf";')
