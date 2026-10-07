from scanner.normalize import job_key, norm_location, norm_text


def test_norm_text_strips_punctuation_case_and_req_ids():
    assert norm_text("Software Engineer, I (REQ-123)") == "softwareengineeri"
    assert norm_text("C++ / C#") == "cc"


def test_norm_location_is_city_level():
    assert norm_location("Toronto, ON, Canada") == "toronto"
    assert norm_location("Toronto") == "toronto"


def test_job_key_ignores_case_punctuation_and_req_ids():
    a = job_key("Acme Inc.", "Software Engineer (R-1)", "Toronto, ON")
    b = job_key("acme inc", "Software  Engineer", "toronto")
    assert a == b
    assert a != job_key("Acme Inc.", "Software Engineer", "Vancouver, BC")
