"""RDF/XML import and export for the supported ontology document subset."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote, unquote

from rdflib import Graph, Literal, Namespace, RDF, RDFS, URIRef
from rdflib.namespace import OWL, XSD


ANNOTATION = Namespace("https://utopia.local/vocab/ontology#")
MAX_RDF_BYTES = 2 * 1024 * 1024
PROPERTY_TO_XSD = {
    "string": XSD.string,
    "integer": XSD.integer,
    "decimal": XSD.decimal,
    "double": XSD.double,
    "date": XSD.date,
    "datetime": XSD.dateTime,
    "boolean": XSD.boolean,
    "enum": XSD.string,
}
XSD_TO_PROPERTY = {
    str(XSD.string): "string",
    str(XSD.integer): "integer",
    str(XSD.decimal): "decimal",
    str(XSD.double): "double",
    str(XSD.date): "date",
    str(XSD.dateTime): "datetime",
    str(XSD.boolean): "boolean",
}


def _base_uri(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-") or "ontology"
    return f"https://utopia.local/ontology/{slug}/"


def _local_name(uri: Any) -> str:
    value = str(uri)
    return unquote(value.rstrip("/").rsplit("/", 1)[-1].rsplit("#", 1)[-1])


def _position(graph: Graph, subject: Any) -> int:
    value = graph.value(subject, ANNOTATION.position)
    if value is None:
        return 1_000_000
    try:
        return int(value)
    except (TypeError, ValueError):
        return 1_000_000


def _source_subject(graph: Graph, rdf_type: URIRef, identifier: str, domain=None):
    for subject in graph.subjects(RDF.type, rdf_type):
        if domain is not None and graph.value(subject, RDFS.domain) != domain:
            continue
        candidates = {
            str(value)
            for value in (
                graph.value(subject, ANNOTATION.id),
                graph.value(subject, RDFS.label),
            )
            if value is not None
        }
        candidates.add(_local_name(subject))
        if identifier in candidates:
            return subject
    return None


def _merge_source_graph(document, source_graph: Graph, baseline: dict[str, Any], generated: Graph) -> Graph:
    baseline_base = _base_uri(str(baseline.get("name", "")))
    current_base = _base_uri(str(document.get("name", "")))
    baseline_to_source: dict[Any, Any] = {}
    current_to_source: dict[Any, Any] = {}
    preserve_current_predicates: set[tuple[Any, Any]] = set()
    preserve_baseline_predicates: set[tuple[Any, Any]] = set()

    def register(source, baseline_uri, current_uri):
        if source is None:
            return
        baseline_to_source[baseline_uri] = source
        current_to_source[current_uri] = source

    ontology_sources = list(source_graph.subjects(RDF.type, OWL.Ontology))
    if ontology_sources:
        register(
            ontology_sources[0],
            URIRef(baseline_base),
            URIRef(current_base),
        )

    class_sources: dict[str, Any] = {}
    for entity in baseline.get("entityTypes", []):
        entity_id = str(entity.get("id", ""))
        source = _source_subject(source_graph, OWL.Class, entity_id)
        class_sources[entity_id] = source
        register(
            source,
            URIRef(f"{baseline_base}class/{quote(entity_id, safe='')}"),
            URIRef(f"{current_base}class/{quote(entity_id, safe='')}"),
        )

    relationship_sources: dict[str, Any] = {}
    for relationship in baseline.get("relationships", []):
        relationship_id = str(relationship.get("id", ""))
        source = _source_subject(source_graph, OWL.ObjectProperty, relationship_id)
        relationship_sources[relationship_id] = source
        register(
            source,
            URIRef(f"{baseline_base}relationship/{quote(relationship_id, safe='')}"),
            URIRef(f"{current_base}relationship/{quote(relationship_id, safe='')}"),
        )

    for entity in baseline.get("entityTypes", []):
        entity_id = str(entity.get("id", ""))
        domain = class_sources.get(entity_id)
        for prop in entity.get("properties", []):
            property_name = str(prop.get("name", ""))
            source = _source_subject(source_graph, OWL.DatatypeProperty, property_name, domain)
            current_uri = URIRef(
                f"{current_base}property/{quote(entity_id, safe='')}/{quote(property_name, safe='')}"
            )
            register(
                source,
                URIRef(
                    f"{baseline_base}property/{quote(entity_id, safe='')}/{quote(property_name, safe='')}"
                ),
                current_uri,
            )
            if source is not None:
                expected_range = PROPERTY_TO_XSD.get(prop.get("type", "string"), XSD.string)
                actual_ranges = set(source_graph.objects(source, RDFS.range))
                if actual_ranges and actual_ranges != {expected_range}:
                    preserve_current_predicates.add((current_uri, RDFS.range))
                    preserve_baseline_predicates.add(
                        (
                            URIRef(
                                f"{baseline_base}property/{quote(entity_id, safe='')}/{quote(property_name, safe='')}"
                            ),
                            RDFS.range,
                        )
                    )

    for relationship in baseline.get("relationships", []):
        relationship_id = str(relationship.get("id", ""))
        source_relationship = relationship_sources.get(relationship_id)
        for attribute in relationship.get("attributes", []):
            attribute_name = str(attribute.get("name", ""))
            source = None
            for candidate in source_graph.subjects(RDF.type, OWL.DatatypeProperty):
                if source_graph.value(candidate, ANNOTATION.relationshipAttributeOf) != source_relationship:
                    continue
                value = source_graph.value(candidate, ANNOTATION.id) or source_graph.value(candidate, RDFS.label)
                if (str(value) if value is not None else _local_name(candidate)) == attribute_name:
                    source = candidate
                    break
            register(
                source,
                URIRef(
                    f"{baseline_base}relationship-attribute/{quote(relationship_id, safe='')}/{quote(attribute_name, safe='')}"
                ),
                URIRef(
                    f"{current_base}relationship-attribute/{quote(relationship_id, safe='')}/{quote(attribute_name, safe='')}"
                ),
            )

    baseline_graph = Graph().parse(data=serialize_document(baseline), format="xml")
    baseline_managed_triples = {
        (
            baseline_to_source.get(subject, subject),
            predicate,
            baseline_to_source.get(object_, object_),
        )
        for subject, predicate, object_ in baseline_graph
        if (subject, predicate) not in preserve_baseline_predicates
    }

    merged = Graph()
    for prefix, namespace in source_graph.namespaces():
        merged.bind(prefix, namespace)
    for triple in source_graph:
        if triple not in baseline_managed_triples:
            merged.add(triple)
    for subject, predicate, object_ in generated:
        if (subject, predicate) in preserve_current_predicates:
            continue
        merged.add(
            (
                current_to_source.get(subject, subject),
                predicate,
                current_to_source.get(object_, object_),
            )
        )
    return merged


def serialize_document(
    document: dict[str, Any],
    source_xml: str | None = None,
    source_document: dict[str, Any] | None = None,
) -> str:
    if source_xml and len(source_xml.encode("utf-8")) > MAX_RDF_BYTES:
        raise ValueError(f"RDF/XML exceeds the {MAX_RDF_BYTES // (1024 * 1024)} MB limit.")
    if source_xml and source_document == document:
        return source_xml

    graph = Graph()
    graph.bind("owl", OWL)
    graph.bind("rdfs", RDFS)
    graph.bind("xsd", XSD)
    graph.bind("utopia", ANNOTATION)

    base = _base_uri(str(document.get("name", "")))
    ontology_uri = URIRef(base)
    graph.add((ontology_uri, RDF.type, OWL.Ontology))
    graph.add((ontology_uri, RDFS.label, Literal(document.get("name", ""))))
    if document.get("description"):
        graph.add((ontology_uri, RDFS.comment, Literal(document["description"])))

    classes: dict[str, URIRef] = {}
    for entity_index, entity in enumerate(document.get("entityTypes", [])):
        entity_id = str(entity["id"])
        class_uri = URIRef(f"{base}class/{quote(entity_id, safe='')}")
        classes[entity_id] = class_uri
        graph.add((class_uri, RDF.type, OWL.Class))
        graph.add((class_uri, ANNOTATION.id, Literal(entity_id)))
        graph.add((class_uri, ANNOTATION.position, Literal(entity_index, datatype=XSD.integer)))
        graph.add((class_uri, RDFS.label, Literal(entity.get("name", entity_id))))
        if entity.get("description"):
            graph.add((class_uri, RDFS.comment, Literal(entity["description"])))
        if entity.get("icon"):
            graph.add((class_uri, ANNOTATION.icon, Literal(entity["icon"])))
        if entity.get("color"):
            graph.add((class_uri, ANNOTATION.color, Literal(entity["color"])))

        for property_index, prop in enumerate(entity.get("properties", [])):
            property_uri = URIRef(
                f"{base}property/{quote(entity_id, safe='')}/{quote(str(prop['name']), safe='')}"
            )
            graph.add((property_uri, RDF.type, OWL.DatatypeProperty))
            graph.add((property_uri, ANNOTATION.id, Literal(str(prop["name"]))))
            graph.add((property_uri, ANNOTATION.position, Literal(property_index, datatype=XSD.integer)))
            graph.add((property_uri, RDFS.label, Literal(str(prop["name"]))))
            graph.add((property_uri, RDFS.domain, class_uri))
            property_type = prop.get("type", "string")
            graph.add((property_uri, RDFS.range, PROPERTY_TO_XSD.get(property_type, XSD.string)))
            graph.add((property_uri, ANNOTATION.propertyType, Literal(property_type)))
            if prop.get("isIdentifier"):
                graph.add((property_uri, ANNOTATION.isIdentifier, Literal(True)))
            if prop.get("unit"):
                graph.add((property_uri, ANNOTATION.unit, Literal(prop["unit"])))
            for enum_value in prop.get("values", []):
                graph.add((property_uri, ANNOTATION.enumValue, Literal(str(enum_value))))
            if prop.get("description"):
                graph.add((property_uri, RDFS.comment, Literal(prop["description"])))

    relationships: dict[str, URIRef] = {}
    for relationship_index, relationship in enumerate(document.get("relationships", [])):
        relationship_id = str(relationship["id"])
        relationship_uri = URIRef(f"{base}relationship/{quote(relationship_id, safe='')}")
        relationships[relationship_id] = relationship_uri
        graph.add((relationship_uri, RDF.type, OWL.ObjectProperty))
        graph.add((relationship_uri, ANNOTATION.id, Literal(relationship_id)))
        graph.add((relationship_uri, ANNOTATION.position, Literal(relationship_index, datatype=XSD.integer)))
        graph.add((relationship_uri, RDFS.label, Literal(relationship.get("name", relationship_id))))
        graph.add((relationship_uri, RDFS.domain, classes[str(relationship["from"])]))
        graph.add((relationship_uri, RDFS.range, classes[str(relationship["to"])]))
        graph.add((relationship_uri, ANNOTATION.cardinality, Literal(relationship.get("cardinality", "many-to-many"))))
        if relationship.get("description"):
            graph.add((relationship_uri, RDFS.comment, Literal(relationship["description"])))
        for attribute_index, attribute in enumerate(relationship.get("attributes", [])):
            attribute_uri = URIRef(
                f"{base}relationship-attribute/{quote(relationship_id, safe='')}/{quote(str(attribute['name']), safe='')}"
            )
            graph.add((attribute_uri, RDF.type, OWL.DatatypeProperty))
            graph.add((attribute_uri, ANNOTATION.id, Literal(str(attribute["name"]))))
            graph.add((attribute_uri, ANNOTATION.position, Literal(attribute_index, datatype=XSD.integer)))
            graph.add((attribute_uri, RDFS.label, Literal(str(attribute["name"]))))
            graph.add((attribute_uri, ANNOTATION.relationshipAttributeOf, relationship_uri))
            graph.add((attribute_uri, ANNOTATION.propertyType, Literal(attribute.get("type", "string"))))

    if source_xml:
        source_graph = Graph()
        try:
            source_graph.parse(data=source_xml, format="xml")
        except Exception as error:
            raise ValueError(f"Invalid source RDF/XML: {error}") from error
        graph = _merge_source_graph(document, source_graph, source_document or {}, graph)

    serialized = graph.serialize(format="pretty-xml", encoding="utf-8")
    return serialized.decode("utf-8") if isinstance(serialized, bytes) else serialized


def parse_document(rdf_xml: str) -> tuple[dict[str, Any], list[str]]:
    if len(rdf_xml.encode("utf-8")) > MAX_RDF_BYTES:
        raise ValueError(f"RDF/XML exceeds the {MAX_RDF_BYTES // (1024 * 1024)} MB limit.")

    graph = Graph()
    try:
        graph.parse(data=rdf_xml, format="xml")
    except Exception as error:
        raise ValueError(f"Invalid RDF/XML: {error}") from error

    ontology_nodes = list(graph.subjects(RDF.type, OWL.Ontology))
    ontology_node = ontology_nodes[0] if ontology_nodes else None
    name = str(graph.value(ontology_node, RDFS.label) or "Imported ontology") if ontology_node else "Imported ontology"
    description = str(graph.value(ontology_node, RDFS.comment) or "") if ontology_node else ""

    entities: list[dict[str, Any]] = []
    entity_by_uri: dict[Any, dict[str, Any]] = {}
    class_uris = sorted(
        graph.subjects(RDF.type, OWL.Class),
        key=lambda uri: (_position(graph, uri), str(uri)),
    )
    for class_uri in class_uris:
        entity_id = str(graph.value(class_uri, ANNOTATION.id) or _local_name(class_uri))
        entity = {
            "id": entity_id,
            "name": str(graph.value(class_uri, RDFS.label) or entity_id),
            "description": str(graph.value(class_uri, RDFS.comment) or ""),
            "icon": str(graph.value(class_uri, ANNOTATION.icon) or "box"),
            "color": str(graph.value(class_uri, ANNOTATION.color) or "#168577"),
            "properties": [],
        }
        entities.append(entity)
        entity_by_uri[class_uri] = entity

    warnings: list[str] = []
    relationships: list[dict[str, Any]] = []
    relationship_by_uri: dict[Any, dict[str, Any]] = {}
    relation_uris = sorted(
        graph.subjects(RDF.type, OWL.ObjectProperty),
        key=lambda uri: (_position(graph, uri), str(uri)),
    )
    for relation_uri in relation_uris:
        source_uri = graph.value(relation_uri, RDFS.domain)
        target_uri = graph.value(relation_uri, RDFS.range)
        if source_uri not in entity_by_uri or target_uri not in entity_by_uri:
            warnings.append(f"Skipped object property {_local_name(relation_uri)} with an unsupported domain or range.")
            continue
        relationship_id = str(graph.value(relation_uri, ANNOTATION.id) or _local_name(relation_uri))
        relationship = {
            "id": relationship_id,
            "name": str(graph.value(relation_uri, RDFS.label) or relationship_id),
            "from": entity_by_uri[source_uri]["id"],
            "to": entity_by_uri[target_uri]["id"],
            "cardinality": str(graph.value(relation_uri, ANNOTATION.cardinality) or "many-to-many"),
            "attributes": [],
        }
        relationship_description = graph.value(relation_uri, RDFS.comment)
        if relationship_description is not None:
            relationship["description"] = str(relationship_description)
        relationship["_uri"] = relation_uri
        relationship["_position"] = _position(graph, relation_uri)
        relationships.append(relationship)
        relationship_by_uri[relation_uri] = relationship

    for property_uri in graph.subjects(RDF.type, OWL.DatatypeProperty):
        relationship_uri = graph.value(property_uri, ANNOTATION.relationshipAttributeOf)
        property_name = str(graph.value(property_uri, ANNOTATION.id) or graph.value(property_uri, RDFS.label) or _local_name(property_uri))
        property_type = str(graph.value(property_uri, ANNOTATION.propertyType) or "")
        if relationship_uri is not None:
            relationship = relationship_by_uri.get(relationship_uri)
            if relationship is None:
                warnings.append(f"Skipped relationship attribute {property_name} whose relationship is unsupported.")
                continue
            relationship["attributes"].append(
                {
                    "name": property_name,
                    "type": property_type or "string",
                    "_position": _position(graph, property_uri),
                }
            )
            continue

        domain_uri = graph.value(property_uri, RDFS.domain)
        entity = entity_by_uri.get(domain_uri)
        if entity is None:
            warnings.append(f"Skipped datatype property {property_name} with an unsupported domain.")
            continue
        range_uri = graph.value(property_uri, RDFS.range)
        if property_type and property_type not in PROPERTY_TO_XSD:
            warnings.append(f"Mapped unsupported property type {property_type} for {property_name} to string.")
            property_type = "string"
        if not property_type:
            property_type = XSD_TO_PROPERTY.get(str(range_uri), "string")
            if range_uri is not None and str(range_uri) not in XSD_TO_PROPERTY:
                warnings.append(f"Mapped unsupported datatype for property {property_name} to string.")
        prop: dict[str, Any] = {
            "name": property_name,
            "type": property_type if property_type in PROPERTY_TO_XSD else "string",
        }
        is_identifier = graph.value(property_uri, ANNOTATION.isIdentifier)
        if is_identifier is not None:
            prop["isIdentifier"] = bool(is_identifier)
        unit = graph.value(property_uri, ANNOTATION.unit)
        if unit is not None:
            prop["unit"] = str(unit)
        enum_values = [str(value) for value in graph.objects(property_uri, ANNOTATION.enumValue)]
        if enum_values:
            prop["values"] = enum_values
        comment = graph.value(property_uri, RDFS.comment)
        if comment is not None:
            prop["description"] = str(comment)
        prop["_position"] = _position(graph, property_uri)
        entity["properties"].append(prop)

    for entity in entities:
        entity["properties"].sort(key=lambda prop: (prop.pop("_position"), prop["name"]))
    relationships.sort(key=lambda relationship: (relationship.pop("_position"), relationship["id"]))
    for relationship in relationships:
        relationship.pop("_uri", None)
        relationship["attributes"].sort(key=lambda attribute: (attribute.pop("_position"), attribute["name"]))

    document = {
        "name": name,
        "description": description,
        "entityTypes": entities,
        "relationships": relationships,
    }

    known_types = {OWL.Ontology, OWL.Class, OWL.DatatypeProperty, OWL.ObjectProperty}
    unsupported_types = {
        str(object_type)
        for object_type in graph.objects(None, RDF.type)
        if object_type not in known_types
    }
    if unsupported_types:
        warnings.append("Unsupported RDF types were retained in the source graph: " + ", ".join(sorted(unsupported_types)))
    supported_predicates = {
        RDF.type,
        RDFS.label,
        RDFS.comment,
        RDFS.domain,
        RDFS.range,
        ANNOTATION.id,
        ANNOTATION.position,
        ANNOTATION.icon,
        ANNOTATION.color,
        ANNOTATION.propertyType,
        ANNOTATION.isIdentifier,
        ANNOTATION.unit,
        ANNOTATION.enumValue,
        ANNOTATION.cardinality,
        ANNOTATION.relationshipAttributeOf,
    }
    unsupported_predicates = {
        str(predicate)
        for _, predicate, _ in graph
        if predicate not in supported_predicates
    }
    if unsupported_predicates:
        warnings.append("Unsupported RDF predicates were retained in the source graph: " + ", ".join(sorted(unsupported_predicates)))
    return document, warnings


PROPERTY_TYPES = set(PROPERTY_TO_XSD)