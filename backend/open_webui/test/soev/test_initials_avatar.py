from open_webui.soev.initials_avatar import initials, initials_svg


def test_initials_take_first_and_last_word():
    assert initials('Ada van der Berg') == 'AB'
    assert initials('mika') == 'M'
    assert initials('  ') == '?'
    assert initials(None) == '?'


def test_initials_skip_words_without_a_leading_letter():
    assert initials('(extern) Sami Jansen') == 'SJ'


def test_svg_escapes_markup_and_is_stable_per_seed():
    svg = initials_svg('<b>&x', 'user-1').decode()
    assert '<b>' not in svg
    assert initials_svg('Ada Berg', 'user-1') == initials_svg('Ada Berg', 'user-1')
    assert svg.startswith('<svg') and svg.endswith('</svg>')
