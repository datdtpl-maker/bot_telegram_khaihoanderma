import html
import pathlib
import zipfile


out = pathlib.Path("mau_bai_viet_deriva_bpo_test.docx")

paragraphs = [
    ("Heading1", "Gel Trị Mụn Deriva Bpo Gel - Giải pháp hỗ trợ da mụn"),
    (
        "Normal",
        "Deriva Bpo Gel là sản phẩm hỗ trợ chăm sóc da mụn, phù hợp để đưa vào quy trình chăm sóc da có mụn viêm, mụn đầu đen hoặc mụn ẩn khi được sử dụng đúng cách.",
    ),
    ("Heading2", "Công dụng nổi bật"),
    (
        "Normal",
        "Sản phẩm giúp hỗ trợ làm sạch vùng da mụn, giảm tình trạng bít tắc lỗ chân lông và cải thiện bề mặt da khi dùng đều đặn theo hướng dẫn.",
    ),
    ("Heading3", "Phù hợp với làn da nào?"),
    (
        "Normal",
        "Phù hợp với da dầu, da hỗn hợp thiên dầu và da đang gặp vấn đề về mụn. Với da nhạy cảm, nên thử trên một vùng nhỏ trước khi dùng toàn mặt.",
    ),
    ("Heading2", "Cách sử dụng gợi ý"),
    (
        "Normal",
        "Sau bước làm sạch và lau khô da, lấy một lượng nhỏ sản phẩm thoa lên vùng da cần chăm sóc. Nên dùng với tần suất phù hợp và kết hợp kem dưỡng, kem chống nắng vào ban ngày.",
    ),
    ("Heading3", "Lưu ý khi dùng"),
    (
        "Normal",
        "Không thoa lên vùng da trầy xước hoặc đang kích ứng mạnh. Nếu có cảm giác châm chích kéo dài, nên giảm tần suất hoặc ngưng dùng và tham khảo tư vấn chuyên môn.",
    ),
    ("Heading2", "Kết luận"),
    (
        "Normal",
        "Gel Trị Mụn Deriva Bpo Gel là lựa chọn đáng cân nhắc cho người đang cần một sản phẩm hỗ trợ chăm sóc da mụn trong routine hằng ngày.",
    ),
]

body_parts = []
for style, text in paragraphs:
    escaped = html.escape(text)
    style_xml = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style != "Normal" else ""
    body_parts.append(f"<w:p>{style_xml}<w:r><w:t>{escaped}</w:t></w:r></w:p>")

document_xml = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
    "<w:body>"
    + "".join(body_parts)
    + '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
    '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"/></w:sectPr>'
    "</w:body></w:document>"
)

styles_xml = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
    '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>'
    '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/>'
    '<w:basedOn w:val="Normal"/><w:pPr><w:outlineLvl w:val="0"/></w:pPr><w:rPr><w:b/><w:sz w:val="32"/></w:rPr></w:style>'
    '<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/>'
    '<w:basedOn w:val="Normal"/><w:pPr><w:outlineLvl w:val="1"/></w:pPr><w:rPr><w:b/><w:sz w:val="26"/></w:rPr></w:style>'
    '<w:style w:type="paragraph" w:styleId="Heading3"><w:name w:val="heading 3"/>'
    '<w:basedOn w:val="Normal"/><w:pPr><w:outlineLvl w:val="2"/></w:pPr><w:rPr><w:b/><w:sz w:val="22"/></w:rPr></w:style>'
    "</w:styles>"
)

content_types_xml = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
    "</Types>"
)

rels_xml = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
    "</Relationships>"
)

word_rels_xml = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
    "</Relationships>"
)

with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
    archive.writestr("[Content_Types].xml", content_types_xml)
    archive.writestr("_rels/.rels", rels_xml)
    archive.writestr("word/document.xml", document_xml)
    archive.writestr("word/styles.xml", styles_xml)
    archive.writestr("word/_rels/document.xml.rels", word_rels_xml)

print(out.resolve())
