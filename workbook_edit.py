"""直接改 xlsx 內部 XML：只動被指定的儲存格／新增列，其餘（格式、公式、表格、其他工作表）原封不動。

用在「Excel 最小改動修復」：改員工休息日、請假、週服務計畫的星期／起迄時間，或把過長的連續服務拆成兩段。
公式欄（服務時數、服務分鐘、請假時數）保留公式並同步更新快取值，讓只讀快取值的匯入程式與 Excel 都看到一致的數字。
"""
CAREFLOW_BUILD = '2026.10.18-v19'
from io import BytesIO
import posixpath
from xml.dom import minidom
from zipfile import ZipFile

_NS_R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'


def _nodes(node, name):
    return node.getElementsByTagNameNS('*', name)


def _text(node):
    return ''.join(child.data if child.nodeType == child.TEXT_NODE else _text(child) for child in node.childNodes)


def _column(ref):
    out = 0
    for char in ref:
        if not char.isalpha():
            break
        out = out * 26 + ord(char.upper()) - 64
    return out


def _letters(number):
    out = ''
    while number:
        number, remainder = divmod(number - 1, 26)
        out = chr(65 + remainder) + out
    return out


class Workbook:
    """XLSX 的最小可寫模型（只支援本專案需要的操作）。"""

    def __init__(self, source_bytes):
        with ZipFile(BytesIO(source_bytes)) as original:
            self.infos = list(original.infolist())
            self.comment = original.comment
            self.files = {item.filename: original.read(item.filename) for item in self.infos}
        self.strings = []
        if 'xl/sharedStrings.xml' in self.files:
            self.strings = [_text(n) for n in _nodes(minidom.parseString(self.files['xl/sharedStrings.xml']), 'si')]
        book = minidom.parseString(self.files['xl/workbook.xml'])
        rels = minidom.parseString(self.files['xl/_rels/workbook.xml.rels'])
        targets = {n.getAttribute('Id'): n.getAttribute('Target') for n in _nodes(rels, 'Relationship')}
        self.sheets = {}
        for node in _nodes(book, 'sheet'):
            rid = node.getAttributeNS(_NS_R, 'id')
            target = targets[rid]
            self.sheets[node.getAttribute('name')] = target.lstrip('/') if target.startswith('/') else posixpath.normpath('xl/' + target)
        self.docs = {}

    # ---- 基礎 ----
    def doc(self, sheet):
        path = self.sheets[sheet]
        if path not in self.docs:
            self.docs[path] = minidom.parseString(self.files[path])
        return self.docs[path]

    def value(self, cell):
        if cell.getAttribute('t') == 'inlineStr':
            return _text(cell)
        values = _nodes(cell, 'v')
        raw = _text(values[0]) if values else ''
        return self.strings[int(raw)] if cell.getAttribute('t') == 's' else raw

    def rows(self, sheet):
        return {int(r.getAttribute('r')): r for r in _nodes(self.doc(sheet), 'row')}

    def headers(self, sheet):
        header = self.rows(sheet)[1]
        return {self.value(c).strip(): _column(c.getAttribute('r')) for c in _nodes(header, 'c')}

    def _element(self, doc, name):
        root = doc.documentElement
        return doc.createElementNS(root.namespaceURI, (root.prefix + ':' + name) if root.prefix else name)

    def _cell(self, doc, row, col, template_style=None):
        cells = list(_nodes(row, 'c'))
        cell = next((c for c in cells if _column(c.getAttribute('r')) == col), None)
        if cell is None:
            cell = self._element(doc, 'c')
            cell.setAttribute('r', _letters(col) + row.getAttribute('r'))
            if template_style is not None:
                cell.setAttribute('s', template_style)
            elif cells and cells[-1].hasAttribute('s'):
                cell.setAttribute('s', cells[-1].getAttribute('s'))
            later = next((c for c in cells if _column(c.getAttribute('r')) > col), None)
            row.insertBefore(cell, later) if later else row.appendChild(cell)
        return cell

    def set_cell(self, sheet, row_number, header, value, style_from=None):
        """value: str → 文字；int/float → 數字；None → 清空。有公式的儲存格保留公式、只更新快取值。"""
        doc = self.doc(sheet)
        row = self.rows(sheet)[row_number]
        col = self.headers(sheet)[header]
        cell = self._cell(doc, row, col, style_from)
        formula = [n for n in _nodes(cell, 'f')]
        for child in list(cell.childNodes):
            if child not in formula:
                cell.removeChild(child)
        if value is None or value == '':
            cell.removeAttribute('t')
            return
        if isinstance(value, (int, float)):
            cell.setAttribute('t', 'n')
            node = self._element(doc, 'v')
            node.appendChild(doc.createTextNode(repr(float(value)) if isinstance(value, float) else str(value)))
            cell.appendChild(node)
        else:
            if formula:  # 文字結果的公式：改成純文字，避免快取值與公式不一致
                for f in formula:
                    cell.removeChild(f)
            cell.setAttribute('t', 'inlineStr')
            container = self._element(doc, 'is')
            leaf = self._element(doc, 't')
            leaf.appendChild(doc.createTextNode(str(value)))
            container.appendChild(leaf)
            cell.appendChild(container)

    def get(self, sheet, row_number, header):
        row = self.rows(sheet)[row_number]
        col = self.headers(sheet)[header]
        cell = next((c for c in _nodes(row, 'c') if _column(c.getAttribute('r')) == col), None)
        return '' if cell is None else self.value(cell)

    def table(self, sheet):
        """整張工作表一次讀完：{列號: {標題: 值}}（不含標題列）。大量讀取請用這個，比逐格 get 快很多。"""
        names = {col: name for name, col in self.headers(sheet).items()}
        out = {}
        for number, row in self.rows(sheet).items():
            if number == 1:
                continue
            out[number] = {names[c]: self.value(cell) for cell in _nodes(row, 'c')
                           for c in [_column(cell.getAttribute('r'))] if c in names}
        return out

    def append_row(self, sheet, values, copy_from_row):
        """在最後面新增一列；格式複製 copy_from_row；values 以標題名稱指定，其餘欄位留白。回傳新列號。"""
        doc = self.doc(sheet)
        rows = self.rows(sheet)
        new_number = max(rows) + 1
        template = rows[copy_from_row]
        row = template.cloneNode(False)
        row.setAttribute('r', str(new_number))
        for attr in ('spans',):
            pass
        for cell in _nodes(template, 'c'):
            fresh = self._element(doc, 'c')
            fresh.setAttribute('r', _letters(_column(cell.getAttribute('r'))) + str(new_number))
            if cell.hasAttribute('s'):
                fresh.setAttribute('s', cell.getAttribute('s'))
            row.appendChild(fresh)
        rows[max(rows)].parentNode.appendChild(row)
        for header, value in values.items():
            self.set_cell(sheet, new_number, header, value)
        # dimension 與 table 範圍
        last_col = max(_column(c.getAttribute('r')) for c in _nodes(row, 'c'))
        for dim in _nodes(doc, 'dimension'):
            first = dim.getAttribute('ref').split(':')[0]
            dim.setAttribute('ref', first + ':' + _letters(last_col) + str(new_number))
        path = self.sheets[sheet]
        relation = posixpath.dirname(path) + '/_rels/' + posixpath.basename(path) + '.rels'
        if relation in self.files:
            for link in _nodes(minidom.parseString(self.files[relation]), 'Relationship'):
                if not link.getAttribute('Type').endswith('/table'):
                    continue
                target = link.getAttribute('Target')
                table_path = target.lstrip('/') if target.startswith('/') else posixpath.normpath(posixpath.dirname(path) + '/' + target)
                table = minidom.parseString(self.files[table_path])
                root = table.documentElement
                ref = root.getAttribute('ref')
                new_ref = ref.split(':')[0] + ':' + ref.split(':')[1].rstrip('0123456789') + str(new_number)
                root.setAttribute('ref', new_ref)
                for filt in _nodes(table, 'autoFilter'):
                    filt.setAttribute('ref', new_ref)
                self.files[table_path] = table.toxml(encoding='UTF-8')
        return new_number

    def to_bytes(self):
        for path, doc in self.docs.items():
            self.files[path] = doc.toxml(encoding='UTF-8')
        out = BytesIO()
        with ZipFile(out, 'w') as result:
            result.comment = self.comment
            for item in self.infos:
                result.writestr(item, self.files[item.filename])
        return out.getvalue()


def row_of(book, sheet, header, key):
    """第一個 header 欄值等於 key 的列號（Excel 列號，標題為 1）。"""
    col = book.headers(sheet)[header]
    for number, row in book.rows(sheet).items():
        if number == 1:
            continue
        cell = next((c for c in _nodes(row, 'c') if _column(c.getAttribute('r')) == col), None)
        if cell is not None and book.value(cell).strip() == str(key):
            return number
    return None
