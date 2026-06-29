import os
import re
import uuid
from typing import Any

from rdflib import Graph, Literal, RDF, RDFS, OWL, URIRef
from sqlalchemy.orm import Session

from app.models.entity import Entity
from app.models.relation import Relation


RDF_EXTENSIONS = {
    ".owl": "xml",
    ".rdf": "xml",
    ".ttl": "turtle",
    ".nt": "nt",
    ".n3": "n3",
    ".xml": "xml",
}


def is_owl_like_file(file_path: str) -> bool:
    return os.path.splitext(file_path)[1].lower() in RDF_EXTENSIONS


def import_owl_graph(db: Session, ontology_id: str, file_path: str) -> dict[str, int]:
    """Import OWL/RDF resources as ontology entities and graph relations."""
    graph = Graph()
    fmt = RDF_EXTENSIONS.get(os.path.splitext(file_path)[1].lower())
    graph.parse(file_path, format=fmt)

    existing_entities = {
        _entity_key(entity): entity
        for entity in db.query(Entity).filter(Entity.ontology_id == ontology_id).all()
    }
    iri_to_entity: dict[str, Entity] = {}

    created_entities = 0
    for resource, entity_type in _iter_entity_resources(graph):
        iri = str(resource)
        key = iri or _label_for(graph, resource)
        entity = existing_entities.get(key)
        if entity is None:
            label = _label_for(graph, resource)
            entity = Entity(
                id=str(uuid.uuid4()),
                ontology_id=ontology_id,
                name_cn=label,
                name_en=_local_name(resource),
                type=entity_type,
                description=_comment_for(graph, resource),
                properties={
                    "source": "owl_import",
                    "source_iri": iri,
                },
                confidence=1.0,
            )
            db.add(entity)
            existing_entities[key] = entity
            created_entities += 1
        iri_to_entity[iri] = entity

    db.flush()

    existing_relations = {
        (
            relation.source_entity,
            relation.target_entity,
            relation.type,
            _relation_source_iri(relation.properties),
        )
        for relation in db.query(Relation).filter(Relation.ontology_id == ontology_id).all()
    }

    created_relations = 0
    for source, target, relation_type, predicate in _iter_relations(graph):
        source_entity = iri_to_entity.get(str(source))
        target_entity = iri_to_entity.get(str(target))
        if not source_entity or not target_entity:
            continue

        rel_key = (source_entity.id, target_entity.id, relation_type, str(predicate))
        if rel_key in existing_relations:
            continue

        relation = Relation(
            id=str(uuid.uuid4()),
            ontology_id=ontology_id,
            source_entity=source_entity.id,
            target_entity=target_entity.id,
            type=relation_type,
            properties={
                "source": "owl_import",
                "source_iri": str(predicate),
                "source_label": _label_for(graph, predicate),
            },
            confidence=1.0,
        )
        db.add(relation)
        existing_relations.add(rel_key)
        created_relations += 1

    return {"entities": created_entities, "relations": created_relations}


def _iter_entity_resources(graph: Graph):
    seen: set[str] = set()
    entity_types = (
        (OWL.Class, "Class"),
        (RDFS.Class, "Class"),
        (OWL.NamedIndividual, "Individual"),
        (OWL.ObjectProperty, "ObjectProperty"),
        (OWL.DatatypeProperty, "DatatypeProperty"),
    )
    for rdf_type, entity_type in entity_types:
        for resource in graph.subjects(RDF.type, rdf_type):
            if isinstance(resource, URIRef) and str(resource) not in seen:
                seen.add(str(resource))
                yield resource, entity_type

    for source, predicate, target in graph:
        if isinstance(source, URIRef) and _is_domain_resource(source) and str(source) not in seen:
            seen.add(str(source))
            yield source, "Resource"
        if isinstance(target, URIRef) and _is_domain_resource(target) and str(target) not in seen:
            seen.add(str(target))
            yield target, "Resource"


def _iter_relations(graph: Graph):
    for child, parent in graph.subject_objects(RDFS.subClassOf):
        if isinstance(child, URIRef) and isinstance(parent, URIRef):
            yield child, parent, "subClassOf", RDFS.subClassOf

    for predicate in graph.subjects(RDF.type, OWL.ObjectProperty):
        domains = [item for item in graph.objects(predicate, RDFS.domain) if isinstance(item, URIRef)]
        ranges = [item for item in graph.objects(predicate, RDFS.range) if isinstance(item, URIRef)]
        for source in domains:
            for target in ranges:
                yield source, target, _local_name(predicate) or "relatedTo", predicate

    for source, predicate, target in graph:
        if (
            isinstance(source, URIRef)
            and isinstance(predicate, URIRef)
            and isinstance(target, URIRef)
            and predicate not in {RDF.type, RDFS.subClassOf, RDFS.domain, RDFS.range}
            and _is_domain_resource(source)
            and _is_domain_resource(target)
        ):
            yield source, target, _local_name(predicate) or "relatedTo", predicate


def _entity_key(entity: Entity) -> str:
    props = entity.properties or {}
    return props.get("source_iri") or entity.name_en or entity.name_cn


def _relation_source_iri(properties: Any) -> str | None:
    if isinstance(properties, dict):
        return properties.get("source_iri")
    return None


def _label_for(graph: Graph, resource: URIRef) -> str:
    for label in graph.objects(resource, RDFS.label):
        if isinstance(label, Literal) and str(label).strip():
            return str(label).strip()
    return _local_name(resource) or str(resource)


def _comment_for(graph: Graph, resource: URIRef) -> str | None:
    for comment in graph.objects(resource, RDFS.comment):
        if isinstance(comment, Literal) and str(comment).strip():
            return str(comment).strip()
    return None


def _local_name(resource: URIRef) -> str:
    value = str(resource)
    if "#" in value:
        value = value.rsplit("#", 1)[-1]
    else:
        value = value.rstrip("/").rsplit("/", 1)[-1]
    return re.sub(r"[_\-]+", " ", value).strip()


def _is_domain_resource(resource: URIRef) -> bool:
    iri = str(resource)
    return not (
        iri.startswith(str(RDF))
        or iri.startswith(str(RDFS))
        or iri.startswith(str(OWL))
        or iri.startswith("http://www.w3.org/2001/XMLSchema#")
    )
