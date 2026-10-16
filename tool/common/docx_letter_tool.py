# -*- coding: utf-8 -*-
"""正式函件生成工具（.docx 催缴函 / 律师函预警函 / 律师函 / 温馨提醒函）。

给催费助手与法务助手共用的确定性输出工具：

    letter_type=催缴函 / 律师函预警 / 律师函 / 温馨提醒函
    + 收件人 / 房产 / 欠款金额与期间 / 缴费限期 / 正文
    → skill_outputs/letters/ 下的 .docx 文件

依赖三级兜底（.venv 里没有 python-docx 时也不能断链）：

    1. python-docx（推荐，``pip install python-docx``）
    2. 标准库 OOXML 极简 writer（zipfile 直接写 WordprocessingML）
    3. .txt 纯文本（并说明如何获得正式 .docx）

合规约束（与法务技能 legal_knowledge.md 的红线一致）：
    * 措辞不得威胁恐吓、不得泄露无关隐私；
    * 律师函预警/律师函仅适用于账龄 180 天以上的欠费，通常由催费流程升级触发。
"""

from __future__ import annotations

import os
import zipfile
from datetime import date
from typing import Any, Dict, List, Optional

from ..tools import Tool, ToolParameter

__all__ = ["DocxLetterTool"]

#: 输出目录（相对项目根，与 ppt 技能的 skill_outputs 同级）
DEFAULT_OUTPUT_DIR = os.path.join("skill_outputs", "letters")

#: 函件类型 → 标题 / 开头语气 / 是否法务级
LETTER_TYPES = {
    "温馨提醒函": {
        "title": "物业费温馨提醒函",
        "opening": "尊敬的业主（住户）：您好！",
        "legal": False,
    },
    "催缴函": {
        "title": "物业费催缴函",
        "opening": "尊敬的业主（住户）：",
        "legal": False,
    },
    "律师函预警": {
        "title": "律师函预警通知",
        "opening": "尊敬的业主（住户）：",
        "legal": True,
    },
    "律师函": {
        "title": "律师函",
        "opening": "尊敬的业主（住户）：",
        "legal": True,
    },
}


def _escape_xml(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


class DocxLetterTool(Tool):
    """正式函件生成（.docx），催费/法务链路的文件输出端。"""

    def __init__(self, output_dir: Optional[str] = None):
        super().__init__(
            name="docx_letter",
            description=(
                "生成物业费正式函件 .docx 文件：温馨提醒函、催缴函、律师函预警、律师函。"
                "律师函预警/律师函适用于账龄180天以上欠费，通常由催费流程升级触发。"
                "关键词：催缴函、律师函、函件、书面通知、正式函。"
            ),
        )
        self.tags = ["催缴函", "律师函", "函件", "催费", "法务"]
        self.output_dir = output_dir

    # ------------------------------------------------------------------
    # 参数定义
    # ------------------------------------------------------------------
    def get_params(self) -> List[ToolParameter]:
        return [
            ToolParameter(
                name="letter_type",
                type="string",
                description="函件类型：温馨提醒函 / 催缴函 / 律师函预警 / 律师函",
                required=True,
            ),
            ToolParameter(
                name="recipient",
                type="string",
                description="收件人业主姓名（只写姓氏即可，缺省用「尊敬的业主」）",
                required=False,
            ),
            ToolParameter(
                name="property",
                type="string",
                description="房产信息（小区/楼栋/房号）",
                required=False,
            ),
            ToolParameter(
                name="amount",
                type="string",
                description="欠款金额与欠费期间描述，如「2025-01 至 2025-05 物业费 165.50 元」",
                required=False,
            ),
            ToolParameter(
                name="deadline",
                type="string",
                description="缴费/整改限期（如「2026年10月30日前」）",
                required=False,
            ),
            ToolParameter(
                name="body",
                type="string",
                description="正文段落（\\n\\n 分段；不传则按函件类型用内置模板）",
                required=False,
            ),
            ToolParameter(
                name="output_dir",
                type="string",
                description="输出目录（默认 skill_outputs/letters/）",
                required=False,
            ),
        ]

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------
    def run(self, params: Dict[str, Any]) -> str:
        letter_type = str(params.get("letter_type") or "").strip()
        if letter_type not in LETTER_TYPES:
            return (
                "❌ 不支持的函件类型: " + (letter_type or "(空)") +
                "\n支持: " + " / ".join(LETTER_TYPES)
            )

        spec = LETTER_TYPES[letter_type]
        meta = {
            "type": letter_type,
            "title": spec["title"],
            "opening": spec["opening"],
            "recipient": str(params.get("recipient") or "").strip(),
            "property": str(params.get("property") or "").strip(),
            "amount": str(params.get("amount") or "").strip(),
            "deadline": str(params.get("deadline") or "").strip(),
            "date": date.today().strftime("%Y年%m月%d日"),
        }
        paragraphs = self._compose_paragraphs(meta, params.get("body"))
        title = spec["title"]

        output_dir = self._resolve_output_dir(params.get("output_dir"))
        os.makedirs(output_dir, exist_ok=True)

        safe_type = letter_type.replace("/", "_")
        stamp = date.today().strftime("%Y%m%d")
        recipient_tag = meta["recipient"] or "业主"
        base_name = f"{safe_type}_{recipient_tag}_{stamp}"
        docx_path = os.path.join(output_dir, base_name + ".docx")

        # ---- 三级兜底：python-docx → stdlib OOXML → txt ----
        try:
            from docx import Document  # noqa: PLC0415
        except ImportError:
            try:
                self._write_docx_ooxml(docx_path, title, paragraphs)
                return self._success_message(docx_path, paragraphs, engine="内置OOXML写入器")
            except Exception as exc:  # noqa: BLE001
                txt_path = os.path.join(output_dir, base_name + ".txt")
                self._write_text(txt_path, paragraphs)
                return (
                    f"⚠️ 未安装 python-docx 且 OOXML 写入失败（{exc}），已生成纯文本版：\n"
                    f"📄 {txt_path}\n"
                    f"💡 执行 `pip install python-docx` 后可生成正式 .docx 函件。"
                )

        try:
            self._write_docx_python_docx(docx_path, title, paragraphs)
        except Exception as exc:  # noqa: BLE001
            return f"❌ 生成 .docx 失败: {exc}"
        return self._success_message(docx_path, paragraphs, engine="python-docx")

    # ------------------------------------------------------------------
    # 正文组装（body 缺省时用内置模板，只让 LLM 传短字段）
    # ------------------------------------------------------------------
    def _compose_paragraphs(self, meta: Dict[str, str], body: Any) -> List[str]:
        if body and str(body).strip():
            return [p.strip() for p in str(body).split("\n\n") if p.strip()]

        recipient = meta["recipient"] or "尊敬的业主"
        property_line = f"您名下房产（{meta['property']}）" if meta["property"] else "您名下房产"
        amount_line = f"经核对，{property_line}尚有物业费欠款：{meta['amount']}。" if meta["amount"] else f"经核对，{property_line}存在物业费欠款，具体金额见附页。"
        deadline_line = (
            f"请您于 {meta['deadline']} 前结清相关费用。"
            if meta["deadline"]
            else "请您尽快结清相关费用。"
        )

        if meta["type"] == "温馨提醒函":
            return [
                f"{meta['opening']}物业费是维持小区公共设施正常运行、保洁绿化与秩序维护的基础。"
                f"系统显示您的物业费可能尚未缴清，若已缴付请忽略本提醒。",
                amount_line,
                f"{deadline_line}如有缴费方式疑问或对费用有异议，欢迎随时联系物业服务中心。",
                "感谢您的理解与支持！",
            ]
        if meta["type"] == "催缴函":
            return [
                f"{meta['opening']}根据《民法典》第九百四十四条，业主应当按照约定向物业服务人支付物业费。",
                amount_line,
                f"{deadline_line}逾期未缴的，我司将依据《物业服务合同》约定继续催缴，"
                "并可能产生违约金。",
                "如您对费用有异议，请于收到本函后 7 日内与我司客服中心联系核对；"
                "逾期未提出异议且未缴费的，视为无异议。",
                "感谢您的配合。",
            ]
        if meta["type"] == "律师函预警":
            return [
                f"{meta['opening']}您名下房产的物业费欠款经多次催缴，至今尚未结清。",
                amount_line,
                f"{deadline_line}逾期仍未结清的，我司将委托律师事务所发出正式律师函，"
                "并保留依据《民法典》《民事诉讼法》通过诉讼途径解决的权利，"
                "由此产生的诉讼费、律师费等将依合同约定主张。",
                "本函为正式催缴的最后提醒，恳请您重视并及时处理。",
            ]
        # 律师函
        return [
            f"{meta['opening']}受物业服务企业委托，就您名下房产物业费欠款事宜郑重致函：",
            amount_line,
            f"{deadline_line}逾期未结清的，委托人将依法向人民法院提起诉讼，"
            "并主张违约金及实现债权的合理费用。"
            "诉讼相关文书将依法送达，届时可能影响您的信用记录与相关权益。",
            "为避免讼累与不必要的损失，建议您在收函后尽快与委托人结算，"
            "或就争议部分与我方协商处理。",
        ]

    # ------------------------------------------------------------------
    # 输出目录
    # ------------------------------------------------------------------
    def _resolve_output_dir(self, override: Any) -> str:
        if override:
            return os.path.abspath(str(override))
        if self.output_dir:
            return os.path.abspath(self.output_dir)
        project_root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        return os.path.join(project_root, DEFAULT_OUTPUT_DIR)

    def _success_message(self, path: str, paragraphs: List[str], engine: str) -> str:
        preview = " / ".join(p[:40] for p in paragraphs[:2])
        return (
            f"✅ 函件已生成: {path}\n"
            f"📝 引擎: {engine}\n"
            f"📄 摘要: {preview}…\n"
            f"共 {len(paragraphs)} 段正文。"
        )

    # ------------------------------------------------------------------
    # 一级：python-docx
    # ------------------------------------------------------------------
    def _write_docx_python_docx(self, path: str, title: str, paragraphs: List[str]) -> None:
        from docx import Document  # noqa: PLC0415
        from docx.enum.text import WD_ALIGN_PARAGRAPH  # noqa: PLC0415
        from docx.shared import Pt  # noqa: PLC0415

        doc = Document()

        heading = doc.add_paragraph()
        heading.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = heading.add_run(title)
        run.bold = True
        run.font.size = Pt(16)

        for text in paragraphs:
            para = doc.add_paragraph(text)
            for run in para.runs:
                run.font.size = Pt(12)

        doc.save(path)

    # ------------------------------------------------------------------
    # 二级：标准库 OOXML 极简 writer（.docx = zip + WordprocessingML）
    # ------------------------------------------------------------------
    def _write_docx_ooxml(self, path: str, title: str, paragraphs: List[str]) -> None:
        # 标题段（居中加粗）+ 正文段
        title_xml = (
            '<w:p><w:pPr><w:jc w:val="center"/></w:pPr>'
            f'<w:r><w:rPr><w:b/><w:sz w:val="32"/></w:rPr>'
            f'<w:t xml:space="preserve">{_escape_xml(title)}</w:t></w:r></w:p>'
        )
        body_xml = "".join(
            '<w:p><w:r><w:rPr><w:sz w:val="24"/></w:rPr>'
            f'<w:t xml:space="preserve">{_escape_xml(p)}</w:t></w:r></w:p>'
            for p in paragraphs
        )

        document_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            f"<w:body>{title_xml}{body_xml}</w:body></w:document>"
        )
        content_types = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            "</Types>"
        )
        rels = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            "</Relationships>"
        )
        word_rels = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>'
        )

        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("[Content_Types].xml", content_types)
            zf.writestr("_rels/.rels", rels)
            zf.writestr("word/document.xml", document_xml)
            zf.writestr("word/_rels/document.xml.rels", word_rels)

    # ------------------------------------------------------------------
    # 三级：纯文本
    # ------------------------------------------------------------------
    def _write_text(self, path: str, paragraphs: List[str]) -> None:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("\n\n".join(paragraphs))
            handle.write("\n")
