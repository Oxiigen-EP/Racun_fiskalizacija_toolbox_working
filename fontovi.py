"""Registracija fonta s podrškom za hrvatska slova (PDF)."""
import os


def get_font():
    """Registrira i vraća naziv fonta s podrškom za hrvatska slova."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    import reportlab

    # DejaVu koji dolazi s reportlabom — uvijek dostupan
    reportlab_font_dir = os.path.join(
        os.path.dirname(reportlab.__file__), 'fonts')

    # Kandidati — traži redom, prvi pronađeni par (regular + bold) pobijedi
    # Svaki unos: (ime_fonta, putanja_regular, putanja_bold)
    font_candidates = [
        # ReportLab ugrađeni — najpouzdanije
        # (
        #     "DejaVuSans",
        #     os.path.join(reportlab_font_dir, "DejaVuSans.ttf"),
        #     os.path.join(reportlab_font_dir, "DejaVuSans-Bold.ttf"),
        # ),
        # Windows sistemski fontovi
        (
            "Arial",
            r"C:\Windows\Fonts\arial.ttf",
            r"C:\Windows\Fonts\arialbd.ttf",
        ),
        (
            "Calibri",
            r"C:\Windows\Fonts\calibri.ttf",
            r"C:\Windows\Fonts\calibrib.ttf",
        ),
        (
            "Verdana",
            r"C:\Windows\Fonts\verdana.ttf",
            r"C:\Windows\Fonts\verdanab.ttf",
        ),
        (
            "Tahoma",
            r"C:\Windows\Fonts\tahoma.ttf",
            r"C:\Windows\Fonts\tahomabd.ttf",
        ),
        (
            "TimesNewRoman",
            r"C:\Windows\Fonts\times.ttf",
            r"C:\Windows\Fonts\timesbd.ttf",
        ),
        (
            "Georgia",
            r"C:\Windows\Fonts\georgia.ttf",
            r"C:\Windows\Fonts\georgiab.ttf",
        ),
        (
            "Segoe",
            r"C:\Windows\Fonts\segoeui.ttf",
            r"C:\Windows\Fonts\segoeuib.ttf",
        ),
        # Linux / macOS fallbackovi (ako se aplikacija pokreće i tamo)
        (
            "DejaVuSans",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        ),
        (
            "DejaVuSans",
            "/usr/share/fonts/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
        ),
        (
            "Arial",
            "/Library/Fonts/Arial.ttf",
            "/Library/Fonts/Arial Bold.ttf",
        ),
    ]

    for font_name, regular_path, bold_path in font_candidates:
        if not os.path.exists(regular_path):
            continue

        try:
            pdfmetrics.registerFont(TTFont(font_name, regular_path))

            bold_name = font_name + "-Bold"
            if os.path.exists(bold_path):
                pdfmetrics.registerFont(TTFont(bold_name, bold_path))
            else:
                # Nema bold varijante — koristi regular i za bold
                bold_name = font_name
                print(f"⚠️  Bold varijanta nije pronađena za {font_name}, "
                      f"koristim regular")

            print(f"✅ Font: {font_name} ({regular_path})")
            return font_name, bold_name

        except Exception as e:
            print(f"⚠️  Font {font_name} nije učitan ({regular_path}): {e}")
            continue

    # Apsolutni fallback — Helvetica bez hrvatskih slova
    print("⚠️  Nije pronađen nijedan TTF font s hrvatskim slovima!")
    print("   Instalirajte: pip install reportlab[fonts]")
    return 'Helvetica', 'Helvetica-Bold'
