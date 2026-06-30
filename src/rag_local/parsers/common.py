import re


def is_ts_only_comments_and_whitespace(text: str) -> bool:
    """Verifica si el texto de TypeScript contiene solo comentarios y espacios."""
    text_no_comments = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    text_no_comments = re.sub(r"//.*", "", text_no_comments)
    return text_no_comments.strip() == ""


def is_prisma_only_comments_and_whitespace(text: str) -> bool:
    """Verifica si el texto de Prisma contiene solo comentarios y espacios."""
    text_no_comments = re.sub(r"//.*", "", text)
    return text_no_comments.strip() == ""


def is_html_only_comments_and_whitespace(text: str) -> bool:
    """Verifica si el texto de HTML contiene solo comentarios y espacios."""
    text_no_comments = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    return text_no_comments.strip() == ""


def is_file_empty_or_only_comments(lines: list[str], suffix: str) -> bool:
    """Determina si un archivo contiene solo comentarios y espacios en blanco."""
    text = "".join(lines)
    if not text.strip():
        return True
    if suffix in (".ts", ".js"):
        return is_ts_only_comments_and_whitespace(text)
    elif suffix == ".prisma":
        return is_prisma_only_comments_and_whitespace(text)
    elif suffix == ".html":
        return is_html_only_comments_and_whitespace(text)
    return False
