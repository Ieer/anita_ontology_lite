COMMERCE_TEMPLATE = {
    "name": "Commerce",
    "description": "A small commerce ontology for customers, orders, and products.",
    "entityTypes": [
        {
            "id": "customer",
            "name": "Customer",
            "description": "A person who places orders.",
            "icon": "person",
            "color": "#168577",
            "properties": [
                {"name": "customerId", "type": "string", "isIdentifier": True},
                {"name": "email", "type": "string"},
            ],
        },
        {
            "id": "order",
            "name": "Order",
            "description": "A customer purchase.",
            "icon": "receipt",
            "color": "#b77a24",
            "properties": [
                {"name": "orderId", "type": "string", "isIdentifier": True},
                {"name": "total", "type": "decimal"},
            ],
        },
        {
            "id": "product",
            "name": "Product",
            "description": "An item available for sale.",
            "icon": "box",
            "color": "#4388a5",
            "properties": [
                {"name": "sku", "type": "string", "isIdentifier": True},
                {"name": "price", "type": "decimal"},
            ],
        },
    ],
    "relationships": [
        {
            "id": "customer_places_order",
            "name": "places",
            "from": "customer",
            "to": "order",
            "cardinality": "one-to-many",
            "attributes": [],
        },
        {
            "id": "order_contains_product",
            "name": "contains",
            "from": "order",
            "to": "product",
            "cardinality": "many-to-many",
            "attributes": [{"name": "quantity", "type": "integer"}],
        },
    ],
}


def empty_ontology():
    return {"name": "Untitled ontology", "description": "", "entityTypes": [], "relationships": []}