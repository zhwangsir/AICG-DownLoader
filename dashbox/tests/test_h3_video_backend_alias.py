from novelvideo.generators.video_generator import parse_newapi_video_backend


def test_h3_alias_maps_to_minimax_h3():
    assert parse_newapi_video_backend("h3") == "MiniMax-H3"
    assert parse_newapi_video_backend("minimax-h3") == "MiniMax-H3"
    assert parse_newapi_video_backend("newapi_MiniMax-H3") == "MiniMax-H3"
    assert parse_newapi_video_backend("MiniMax-H3") == "MiniMax-H3"


def test_ltx_alias_maps_to_ltx25():
    assert parse_newapi_video_backend("ltx") == "LTX-2.5"
    assert parse_newapi_video_backend("LTX-2.5") == "LTX-2.5"
    assert parse_newapi_video_backend("newapi_LTX-2.5") == "LTX-2.5"
