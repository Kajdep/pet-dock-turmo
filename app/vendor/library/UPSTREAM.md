# Vendored upstream subset

Source: [mathoudebine/turing-smart-screen-python](https://github.com/mathoudebine/turing-smart-screen-python/)

Pinned upstream commit: `2b33ab4f00a096916dd6a1174441a53a7ec33b03`.

The following upstream files are included unchanged:

- `library/lcd/lcd_comm.py`
- `library/lcd/lcd_comm_rev_a.py`
- `library/lcd/lcd_comm_rev_b.py`
- `library/lcd/serialize.py`
- `library/lcd/color.py`
- `LICENSE`, `AUTHORS`, and `COPYRIGHT`

`library/__init__.py` and `library/lcd/__init__.py` are package markers added for
this application. `library/log.py` is a reduced derivative: it exposes the
upstream `logger` name but intentionally does not call `logging.basicConfig` or
create `log.log`; application logging remains owned by the caller.

The application subclasses `LcdCommRevA` and `LcdCommRevB` in
`../../display_io.py` rather than modifying protocol files. Those subclasses
replace upstream serial opening/retry/reopen behavior with one bounded serial
open and descriptive exceptions. No reset, firmware, or driver operations are
performed.

This vendored subset is GPL-3.0-or-later; see `LICENSE` and upstream notices.
