import html
import pathlib
import zipfile


out = pathlib.Path("san_pham_moi_test_khd_barrier_repair.docx")

paragraphs = [
    ("Heading1", "Serum Phục Hồi Da KHD Barrier Repair 30ml"),
    (
        "Normal",
        "Serum Phục Hồi Da KHD Barrier Repair 30ml là sản phẩm chăm sóc da hỗ trợ làm dịu, cấp ẩm và phục hồi hàng rào bảo vệ da sau mụn, sau treatment hoặc khi da đang yếu.",
    ),
    ("Heading2", "Giới thiệu sản phẩm"),
    (
        "Normal",
        "Sản phẩm có kết cấu serum mỏng nhẹ, dễ thấm, phù hợp với làn da cần phục hồi nhưng vẫn muốn cảm giác thông thoáng, không nặng mặt.",
    ),
    ("Heading2", "Công dụng nổi bật"),
    ("Heading3", "Hỗ trợ phục hồi da yếu"),
    (
        "Normal",
        "Công thức tập trung vào khả năng bổ sung độ ẩm và làm dịu da, giúp giảm cảm giác khô căng sau các bước chăm sóc da có hoạt chất.",
    ),
    ("Heading3", "Phù hợp da sau treatment"),
    (
        "Normal",
        "Có thể dùng trong routine phục hồi sau khi sử dụng BHA, retinoid hoặc peel da nhẹ, tùy theo tình trạng da thực tế.",
    ),
    ("Heading3", "Cấp ẩm nhẹ, không gây bí da"),
    (
        "Normal",
        "Kết cấu dễ tán và thấm nhanh, phù hợp với da dầu, da hỗn hợp và da dễ nổi mụn cần một bước cấp ẩm nhẹ nhàng.",
    ),
    ("Heading2", "Cách sử dụng"),
    (
        "Normal",
        "Sau bước làm sạch và toner, lấy 2-3 giọt serum thoa đều lên mặt. Dùng sáng và tối. Ban ngày nên kết hợp kem chống nắng.",
    ),
    ("Heading2", "Đối tượng phù hợp"),
    ("Heading3", "Da mụn sau treatment"),
    (
        "Normal",
        "Phù hợp với làn da đang phục hồi sau mụn hoặc sau khi dùng hoạt chất treatment.",
    ),
    ("Heading3", "Da nhạy cảm, dễ khô căng"),
    (
        "Normal",
        "Có thể dùng khi da thiếu ẩm, bong nhẹ hoặc cần làm dịu.",
    ),
    ("Heading2", "Lưu ý khi sử dụng"),
    (
        "Normal",
        "Không dùng trên vùng da đang trầy xước hoặc kích ứng nặng. Ngưng sử dụng nếu có dấu hiệu bất thường và tham khảo ý kiến chuyên viên.",
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
