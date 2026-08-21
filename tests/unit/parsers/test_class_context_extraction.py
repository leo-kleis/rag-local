import json

from rag_local.parsers.html import extract_html_class_parents
from rag_local.parsers.typescript.ast import extract_jsx_class_parents


def test_extract_jsx_dynamic_text_children():
    """Verifica que jsx_expression en posición de hijo marque has_dynamic_text: True."""
    code = """
    export function ChatBox() {
        return (
            <div className="chat-feed">
                <div className="chat-bubble">
                    {msg.text}
                    <span className="timestamp">12:00</span>
                </div>
            </div>
        );
    }
    """
    res = json.loads(extract_jsx_class_parents(code))
    assert "chat-bubble" in res
    assert res["chat-bubble"]["has_dynamic_text"] is True
    assert "chat-feed" in res["chat-bubble"]["parents"]

    assert "timestamp" in res
    assert res["timestamp"]["has_dynamic_text"] is False
    assert "chat-bubble" in res["timestamp"]["parents"]


def test_extract_jsx_attributes_do_not_trigger_dynamic_text():
    """Verifica que expresiones dentro de atributos no marquen has_dynamic_text."""
    code = """
    export function Badge({ count }) {
        return (
            <div className={count > 0 ? "tab-badge is-active" : "tab-badge"}>
                15
            </div>
        );
    }
    """
    res = json.loads(extract_jsx_class_parents(code))
    assert "tab-badge" in res
    assert res["tab-badge"]["has_dynamic_text"] is False


def test_extract_jsx_nested_components_do_not_trigger_dynamic_text():
    """Verifica que componentes anidados no se tomen como texto dinámico."""
    code = """
    export function Header({ user, showIcon }) {
        return (
            <div className="header-bar">
                <div className="icon-wrapper">
                    {<Icon />}
                    {showIcon && <Avatar />}
                    {HeaderTitle}
                </div>
            </div>
        );
    }
    """
    res = json.loads(extract_jsx_class_parents(code))
    assert "icon-wrapper" in res
    assert res["icon-wrapper"]["has_dynamic_text"] is False


def test_extract_jsx_collections_map_vs_foreach():
    """Verifica que .map() marque is_collection: True y .forEach() quede en False."""
    code_map = """
    export function ItemList({ items }) {
        return (
            <div className="list-container">
                {items.map(item => (
                    <div className="card-item" key={item.id}>
                        {item.title}
                    </div>
                ))}
            </div>
        );
    }
    """
    res_map = json.loads(extract_jsx_class_parents(code_map))
    assert "card-item" in res_map
    assert res_map["card-item"]["is_collection"] is True
    assert res_map["card-item"]["has_dynamic_text"] is True

    code_foreach = """
    export function DoSomething({ items }) {
        items.forEach(i => {
            const el = <div className="static-item">Static</div>;
        });
    }
    """
    res_foreach = json.loads(extract_jsx_class_parents(code_foreach))
    assert "static-item" in res_foreach
    assert res_foreach["static-item"]["is_collection"] is False


def test_extract_html_angular_signals():
    """Verifica que interpolaciones {{ }} y *ngFor marquen flags en HTML/Angular."""
    html_code = """
    <div class="user-feed">
        <div class="user-card" *ngFor="let user of users">
            <h3 class="user-name">{{ user.name }}</h3>
            <p class="user-bio" [innerText]="user.bio"></p>
            <span class="static-label">Perfil</span>
        </div>
    </div>
    """
    res = json.loads(extract_html_class_parents(html_code))

    assert "user-card" in res
    assert res["user-card"]["is_collection"] is True

    assert "user-name" in res
    assert res["user-name"]["has_dynamic_text"] is True
    assert "user-card" in res["user-name"]["parents"]

    assert "user-bio" in res
    assert res["user-bio"]["has_dynamic_text"] is True

    assert "static-label" in res
    assert res["static-label"]["has_dynamic_text"] is False


def test_jsx_own_tags_extracted_correctly():
    """Verifica own_tags con los tres casos que importan: tag nativo, self-closing, PascalCase.

    Bug corregido: children[0] era '<' (token), no el tag.
    Fix: child_by_field_name('name') + verificar original_tag[0].islower()
    antes de bajar a minúsculas (Icon → 'I'.islower() = False → excluido).
    """
    code = """
    export function Widget() {
        return (
            <div className="wrap">
                <img className="tab-icon" src="x.svg" />
                <Icon className="also-icon" />
            </div>
        );
    }
    """
    res = json.loads(extract_jsx_class_parents(code))

    # Caso 1: tag nativo abierto — own_tags debe contener 'div'
    assert "wrap" in res
    assert res["wrap"]["own_tags"] == ["div"]

    # Caso 2: self-closing nativo (<img />) — propiedad nativa, debe registrarse
    assert "tab-icon" in res
    assert res["tab-icon"]["own_tags"] == ["img"]

    # Caso 3: componente PascalCase (<Icon />) — debe estar en el dict (tiene className)
    # pero own_tags debe estar vacío porque 'I'.islower() = False
    assert "also-icon" in res
    assert res["also-icon"]["own_tags"] == []


def test_html_own_tags_extracted_correctly():
    """Verifica que own_tags contiene el tag HTML real en el parser HTML/Angular."""
    html_code = """
    <section class="page-wrapper">
        <ul class="nav-list">
            <li class="nav-item">
                <a class="nav-link" href="#">Home</a>
            </li>
        </ul>
        <input class="search-box" type="text" />
    </section>
    """
    res = json.loads(extract_html_class_parents(html_code))

    assert "page-wrapper" in res
    assert "section" in res["page-wrapper"]["own_tags"]

    assert "nav-list" in res
    assert "ul" in res["nav-list"]["own_tags"]

    assert "nav-item" in res
    assert "li" in res["nav-item"]["own_tags"]

    assert "nav-link" in res
    assert "a" in res["nav-link"]["own_tags"]

    # input es void (no entra al stack, pero sí se registra en own_tags)
    assert "search-box" in res
    assert "input" in res["search-box"]["own_tags"]
