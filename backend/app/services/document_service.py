import os

def convert_to_markdown(file_path: str, mime_type: str) -> str:
    ext = os.path.splitext(file_path)[1].lower()

    # For plain text formats, read directly — no conversion needed
    if ext in ('.md', '.txt', '.owl', '.rdf', '.ttl', '.nt', '.n3') or (mime_type and ('text/plain' in mime_type or 'text/markdown' in mime_type or 'rdf' in mime_type or 'turtle' in mime_type)):
        try:
            with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                return f.read()
        except Exception as e:
            return f'[Text read failed: {e}]'

    # For CSV, read directly and format as markdown table
    if ext == '.csv' or (mime_type and 'csv' in mime_type):
        try:
            with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                lines = f.read().splitlines()
            if not lines:
                return ''
            # First line as header
            header = lines[0]
            separator = '|'.join(['---'] * (header.count(',') + 1))
            md_lines = ['| ' + header.replace(',', ' | ') + ' |',
                        '| ' + separator + ' |']
            for row in lines[1:]:
                md_lines.append('| ' + row.replace(',', ' | ') + ' |')
            return '\n'.join(md_lines)
        except Exception as e:
            return f'[CSV read failed: {e}]'

    # For binary formats use markitdown
    try:
        from markitdown import MarkItDown
        md = MarkItDown()
        result = md.convert(file_path)
        return result.text_content or ''
    except BaseException as e:
        # Fallback for any text-like content
        if mime_type and ('text' in mime_type or 'csv' in mime_type):
            try:
                with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                    return f.read()
            except Exception:
                pass
        return f'[File conversion failed: {e}]'
