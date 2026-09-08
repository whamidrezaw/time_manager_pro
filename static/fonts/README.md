# Fonts

Vazirmatn v33.003, by Saber Rastikerdar — https://github.com/rastikerdar/vazirmatn
Licensed under the SIL Open Font License 1.1.

Three weights are committed because the share card in `app/services/cards.py`
draws with them at render time. They cover Persian and Latin from one family,
so a card never falls back to a second typeface halfway through a line.

Emoji are deliberately absent: Pillow cannot draw colour emoji, so the card
uses worded labels instead of pictograms.
