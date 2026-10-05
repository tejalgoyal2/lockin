from scanner.text import html_to_text


def test_paragraphs_and_lists():
    html = "<p>About the role</p><p>Requirements</p><ul><li>Python</li><li>SQL</li></ul><p>Apply now</p>"
    assert html_to_text(html) == "About the role\n\nRequirements\n- Python\n- SQL\n\nApply now"


def test_entities_and_whitespace():
    assert html_to_text("<div>R&amp;D&nbsp;team \n  rocks</div>") == "R&D team rocks"


def test_greenhouse_double_escaped_content():
    escaped = "&lt;p&gt;Build &amp;amp; ship&lt;/p&gt;&lt;p&gt;Second&lt;/p&gt;"
    assert html_to_text(escaped, unescape_first=True) == "Build & ship\n\nSecond"


def test_scripts_dropped_and_empty():
    assert html_to_text("<style>x{}</style><script>alert(1)</script>hi") == "hi"
    assert html_to_text("") == ""
