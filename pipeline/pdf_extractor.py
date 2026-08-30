from pathlib import Path

import pymupdf


def extract_pdf_pages(
    pdf_path,
    start_page,
    end_page,
    output_file,
):
    """
    Seçilen fiziksel PDF sayfalarındaki metni çıkarır.

    start_page ve end_page kullanıcı açısından 1 tabanlıdır.
    PyMuPDF sayfa indeksleri ise 0 tabanlıdır.
    """

    pdf_path = Path(pdf_path)
    output_file = Path(output_file)

    if not pdf_path.exists():
        raise FileNotFoundError(
            f"PDF bulunamadı: {pdf_path}"
        )

    output_file.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    extracted_pages = []
    pages_without_text = []

    with pymupdf.open(pdf_path) as document:
        total_pages = len(document)

        if start_page < 1:
            raise ValueError(
                "Başlangıç sayfası en az 1 olmalıdır."
            )

        if end_page > total_pages:
            raise ValueError(
                f"Bitiş sayfası PDF sayısını aşıyor: "
                f"{end_page} > {total_pages}"
            )

        if start_page > end_page:
            raise ValueError(
                "Başlangıç sayfası bitiş sayfasından "
                "büyük olamaz."
            )

        for page_number in range(
            start_page,
            end_page + 1,
        ):
            page_index = page_number - 1
            page = document[page_index]

            page_text = page.get_text(
                "text",
                sort=True,
            ).strip()

            if not page_text:
                pages_without_text.append(
                    page_number
                )

            extracted_pages.append(
                {
                    "page_number": page_number,
                    "text": page_text,
                }
            )

    output_parts = []

    for page_data in extracted_pages:
        page_number = page_data["page_number"]
        page_text = page_data["text"]

        output_parts.append(
            "=" * 68
        )
        output_parts.append(
            f"PDF SAYFASI: {page_number}"
        )
        output_parts.append(
            "=" * 68
        )
        output_parts.append("")

        if page_text:
            output_parts.append(page_text)
        else:
            output_parts.append(
                "[Bu sayfadan okunabilir metin çıkarılamadı.]"
            )

        output_parts.append("")

    extracted_text = "\n".join(
        output_parts
    ).strip()

    output_file.write_text(
        extracted_text,
        encoding="utf-8",
    )

    result = {
        "pdf_path": str(pdf_path),
        "output_file": str(output_file),
        "start_page": start_page,
        "end_page": end_page,
        "total_selected_pages": (
            end_page - start_page + 1
        ),
        "characters": len(extracted_text),
        "pages_without_text": pages_without_text,
    }

    return result