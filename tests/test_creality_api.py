"""The API read path parses real answers and refuses to invent the rest.

Fixtures here are the shapes the probe actually recorded on 2026-10-24
(`debug/api_deep_*`, verbatim field names, values shortened where noted). No
browser, no network: `creality_api`'s parsers are pure so these run in the
normal suite, and the licence stays decided by `downloader.classify_license`.
"""
import pytest

import creality_api as api


# the model node PAYLOAD_JS returns for /model-detail/modular-mobile-organizer
NODE = {
    "nodeId": "68b9fb618d1f79abe1da2e80",
    "groupName": "Modular Phone Organizer",
    "nickName": "Litolunar",
    "userId": "4668946391",
    "license": "CC BY",
    "totalFileSize": "102220",
    "shareId": "67407336",
    "urlAlias": "modular-mobile-organizer",
    "desc": "A phone organizer that holds three handsets.",
    "groupDesc": '<div class="editor-item"><h3>Modular Phone</h3>'
                 '<img src="https://pic2-cdn.creality.com/pic/promo.png"></div>',
    "covers": ["https://pic2-cdn.creality.com/pic/a.png",
               "https://pic2-cdn.creality.com/pic/b.png"],
    "editCovers": ["https://pic2-cdn.creality.com/pic/b.png",
                   "https://pic2-cdn.creality.com/pic/c.png"],
}


def _walk(node=None, **kw):
    out = {"missing": False, "nodes": 240, "h1": "Modular Phone Organizer",
           "title": "Modular Phone Organizer | Creality Cloud", "dead": False,
           "error": ""}
    out["model"] = NODE if node is None else node
    out.update(kw)
    return out


def test_payload_yields_the_publish_fields():
    rec = api.parse_payload(_walk())
    assert rec["name"] == "Modular Phone Organizer"
    assert rec["author"] == "Litolunar"
    assert rec["license_raw"] == "CC BY"
    assert rec["model_id"] == "68b9fb618d1f79abe1da2e80"
    assert rec["share_id"] == "67407336"
    assert rec["total_file_size"] == 102220
    assert rec["alias"] == "modular-mobile-organizer"
    assert rec["dead"] is False
    assert rec["unknown"] == []


def test_gallery_is_the_cover_urls_in_order_and_deduped():
    rec = api.parse_payload(_walk())
    assert rec["covers"] == ["https://pic2-cdn.creality.com/pic/a.png",
                             "https://pic2-cdn.creality.com/pic/b.png",
                             "https://pic2-cdn.creality.com/pic/c.png"]


def test_cover_urls_inside_the_description_html_are_not_photos():
    # groupDesc carries <img> promo art; it must never enter the gallery set
    rec = api.parse_payload(_walk())
    assert all("promo" not in u for u in rec["covers"])


def test_missing_licence_is_reported_not_invented():
    node = dict(NODE)
    node.pop("license")
    rec = api.parse_payload(_walk(node))
    assert rec["license_raw"] == ""
    assert "license_raw" in rec["unknown"]
    # and the caller's judge refuses an empty licence rather than allowing it
    assert api.license_of(rec["license_raw"])["ok_for_reupload"] is False


def test_author_falls_back_to_username_only_when_nickname_is_absent():
    node = dict(NODE)
    node.pop("nickName")
    node["userName"] = "litolunar-handle"
    assert api.parse_payload(_walk(node))["author"] == "litolunar-handle"


def test_dead_listing_is_named_as_dead_not_as_an_empty_model():
    rec = api.parse_payload({"missing": False, "nodes": 240, "model": None,
                             "h1": "Page Not Found", "title": "", "dead": True,
                             "error": "404"})
    assert rec["dead"] is True
    assert rec["name"] == ""


def test_no_payload_means_unknown_not_a_guess():
    rec = api.parse_payload({"missing": True, "h1": "", "title": ""})
    assert "payload" in rec["unknown"]
    assert rec["name"] == "" and rec["covers"] == []


def test_search_card_reads_name_author_licence_and_covers():
    item = {"id": "68b9fb618d1f79abe1da2e80", "shareId": 67407336,
            "groupName": "Modular Phone Organizer", "license": "CC BY",
            "totalFileSize": 102220, "urlAlias": "modular-mobile-organizer",
            "userInfo": {"nickName": "Litolunar"},
            "covers": [{"url": "https://pic2-cdn.creality.com/pic/a.png"},
                       {"originUrl": "https://pic2-cdn.creality.com/pic/b.png"}]}
    rec = api.parse_search_item(item)
    assert rec["author"] == "Litolunar"
    assert rec["model_id"] == "68b9fb618d1f79abe1da2e80"
    assert len(rec["covers"]) == 2


def test_file_list_reads_the_real_row_and_keeps_the_cxbin_key():
    payload = {"code": 0, "msg": "ok", "result": {"count": 1, "list": [{
        "id": "68b9fb618d1f79abe1da2e86", "fileName": "Organizador de moviles",
        "fileSize": 102220, "fileFormat": ".3mf",
        "fileKey": "model/1e082ac0a1477961a639d8931d7af3b7.cxbin"}]}}
    got = api.parse_file_list(payload)
    assert got["shape_ok"] is True
    assert got["files"][0]["format"] == ".3mf"
    assert got["files"][0]["size"] == 102220
    assert got["files"][0]["key"].endswith(".cxbin")


def test_file_list_shape_drift_is_refused_not_read_as_no_files():
    assert api.parse_file_list({"code": 0, "result": {"items": []}})["shape_ok"] is False
    assert api.parse_file_list({})["shape_ok"] is False


def test_3mf_list_gives_the_id_download_accepts():
    payload = {"code": 0, "result": {"list": [
        {"id": "68b9fb618d1f79abe1da2e8a", "name": "Organizador de moviles.3mf"}]}}
    got = api.parse_3mf_list(payload)
    assert got["ids"] == ["68b9fb618d1f79abe1da2e8a"]
    assert got["shape_ok"] is True


def test_3mf_list_empty_is_a_normal_answer():
    got = api.parse_3mf_list({"code": 0, "result": {"list": []}})
    assert got == {"ids": [], "names": [], "shape_ok": True}


def test_download_url_is_unescaped_and_required_to_be_http():
    payload = {"code": 0, "result": {
        "downloadUrl": "https://internal-creality-usa.oss-accelerate.aliyuncs.com/"
                       "file3mf%2Fa.3mf?Expires=1&amp;OSSAccessKeyId=k&amp;Signature=s"}}
    got = api.parse_download(payload)
    assert "&amp;" not in got["url"]
    assert got["url"].count("&") == 2
    assert api.parse_download({"result": {"downloadUrl": "javascript:alert(1)"}})["url"] == ""
    assert api.parse_download({})["url"] == ""


@pytest.mark.parametrize("raw,allowed", [
    ("CC BY", True), ("CC BY-SA", True), ("CC0", True),
    ("CC BY-NC-ND", False), ("CC BY-ND", False), ("CC BY-NC", False),
    ("CXY-SL", False), ("", False),
])
def test_licence_is_decided_by_the_existing_judge(raw, allowed):
    # the API layer must not grow its own opinion: these are classify_license's
    assert api.license_of(raw)["ok_for_reupload"] is allowed


def test_parse_error_keeps_the_site_code_and_message():
    got = api.parse_error({"code": 517, "msg": "The item does not exist"})
    assert got == {"code": 517, "msg": "The item does not exist"}
