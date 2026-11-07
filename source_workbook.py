"""Update reviewed owners/weekly clocks while preserving unrelated XLSX bytes.

    Formula nodes and cached values are retained. No spreadsheet recalculation,
    original-file overwrite or additional package is needed.
"""
from io import BytesIO
import posixpath
from xml.dom import minidom
from zipfile import ZipFile


def _nodes(node,name):
    return node.getElementsByTagNameNS('*',name)


def _text(node):
    return ''.join(child.data if child.nodeType==child.TEXT_NODE else _text(child)
                   for child in node.childNodes)


def _column(ref):
    out=0
    for char in ref:
        if not char.isalpha():break
        out=out*26+ord(char.upper())-64
    return out


def _letters(number):
    out=''
    while number:
        number,remainder=divmod(number-1,26);out=chr(65+remainder)+out
    return out


def update_source_workbook(source_bytes,owners,weekly_changes=None,backups=None):
    with ZipFile(BytesIO(source_bytes)) as original:
        files={item.filename:original.read(item.filename) for item in original.infolist()}
        strings=[]
        if 'xl/sharedStrings.xml' in files:
            strings=[_text(node) for node in _nodes(minidom.parseString(files['xl/sharedStrings.xml']),'si')]
        book=minidom.parseString(files['xl/workbook.xml'])
        rels=minidom.parseString(files['xl/_rels/workbook.xml.rels'])
        targets={n.getAttribute('Id'):n.getAttribute('Target') for n in _nodes(rels,'Relationship')}
        sheets={}
        for node in _nodes(book,'sheet'):
            rid=node.getAttributeNS('http://schemas.openxmlformats.org/officeDocument/2006/relationships','id')
            target=targets[rid]
            sheets[node.getAttribute('name')]=target.lstrip('/') if target.startswith('/') else posixpath.normpath('xl/'+target)
        def value(cell):
            if cell.getAttribute('t')=='inlineStr':return _text(cell)
            values=_nodes(cell,'v');raw=_text(values[0]) if values else ''
            return strings[int(raw)] if cell.getAttribute('t')=='s' else raw
        def element(doc,name):
            root=doc.documentElement
            tag=(root.prefix+':'+name) if root.prefix else name
            return doc.createElementNS(root.namespaceURI,tag)
        def set_cell(doc,row,col,text,numeric=False):
            cells=list(_nodes(row,'c'))
            cell=next((c for c in cells if _column(c.getAttribute('r'))==col),None)
            if cell is None:
                cell=element(doc,'c');cell.setAttribute('r',_letters(col)+row.getAttribute('r'))
                if cells and cells[-1].hasAttribute('s'):cell.setAttribute('s',cells[-1].getAttribute('s'))
                later=next((c for c in cells if _column(c.getAttribute('r'))>col),None)
                row.insertBefore(cell,later) if later else row.appendChild(cell)
            for child in list(cell.childNodes):cell.removeChild(child)
            cell.setAttribute('t','n' if numeric else 'inlineStr')
            container=element(doc,'v' if numeric else 'is')
            leaf=container if numeric else element(doc,'t')
            leaf.appendChild(doc.createTextNode(str(text)))
            if not numeric:container.appendChild(leaf)
            cell.appendChild(container)
        path=sheets['個案資料'];doc=minidom.parseString(files[path]);rows=list(_nodes(doc,'row'))
        def write_column(name,values):
            header=next(r for r in rows if r.getAttribute('r')=='1')
            headers={value(c):_column(c.getAttribute('r')) for c in _nodes(header,'c')}
            key=next(headers[n] for n in ['內部案號','案家ID','案號'] if n in headers)
            added=name not in headers
            col=headers.get(name,max(headers.values())+1)
            if added:set_cell(doc,header,col,name)
            for row in rows:
                cells=list(_nodes(row,'c'))
                case=next((value(c).strip() for c in cells if _column(c.getAttribute('r'))==key),'')
                if case in values:set_cell(doc,row,col,values[case])
            if added:
                for dim in _nodes(doc,'dimension'):
                    ref=dim.getAttribute('ref');dim.setAttribute('ref',ref.split(':')[0]+':'+_letters(max(col,_column(ref.split(':')[-1])))+str(max(int(r.getAttribute('r')) for r in rows)))
                relation=posixpath.dirname(path)+'/_rels/'+posixpath.basename(path)+'.rels'
                if relation in files:
                    for link in _nodes(minidom.parseString(files[relation]),'Relationship'):
                        if not link.getAttribute('Type').endswith('/table'):continue
                        target=link.getAttribute('Target')
                        table_path=target.lstrip('/') if target.startswith('/') else posixpath.normpath(posixpath.dirname(path)+'/'+target)
                        table=minidom.parseString(files[table_path]);root=table.documentElement
                        ref=root.getAttribute('ref');end=ref.split(':')[-1]
                        if _column(end)!=col-1:continue
                        new_ref=ref.split(':')[0]+':'+_letters(col)+''.join(c for c in end if c.isdigit())
                        root.setAttribute('ref',new_ref)
                        for filt in _nodes(table,'autoFilter'):filt.setAttribute('ref',new_ref)
                        columns=_nodes(table,'tableColumns')[0]
                        child=element(table,'tableColumn');child.setAttribute('id',str(col));child.setAttribute('name',name)
                        columns.appendChild(child);columns.setAttribute('count',str(len(_nodes(columns,'tableColumn'))))
                        files[table_path]=table.toxml(encoding='UTF-8')
        write_column('歷史首選',owners)
        if backups:write_column('歷史備援',backups)
        files[path]=doc.toxml(encoding='UTF-8')
        if weekly_changes:
            path=sheets['週服務計畫'];doc=minidom.parseString(files[path]);rows=list(_nodes(doc,'row'))
            header=next(r for r in rows if r.getAttribute('r')=='1')
            headers={value(c):_column(c.getAttribute('r')) for c in _nodes(header,'c')}
            for row in rows:
                changes=weekly_changes.get(int(row.getAttribute('r')))
                if not changes:continue
                for label in ['開始時間','結束時間']:
                    cell=next((c for c in _nodes(row,'c') if _column(c.getAttribute('r'))==headers[label]),None)
                    numeric=cell is not None and cell.getAttribute('t') not in ('s','str','inlineStr')
                    hour,minute=map(int,changes[label].split(':'))
                    set_cell(doc,row,headers[label],(hour*60+minute)/1440 if numeric else changes[label],numeric)
            files[path]=doc.toxml(encoding='UTF-8')
        out=BytesIO()
        with ZipFile(out,'w') as result:
            result.comment=original.comment
            for item in original.infolist():result.writestr(item,files[item.filename])
        return out.getvalue()
