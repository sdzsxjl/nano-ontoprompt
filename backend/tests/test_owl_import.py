def test_upload_owl_imports_entities_and_relations(client, auth_headers):
    ontology = client.post(
        "/api/v1/ontologies",
        json={"name": "OWL Import", "domain": "鍏朵粬"},
        headers=auth_headers,
    ).json()["data"]

    ttl = b"""
@prefix ex: <http://example.com/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

ex:Product a owl:Class ;
    rdfs:label "Product" .
ex:Supplier a owl:Class ;
    rdfs:label "Supplier" .
ex:PremiumSupplier a owl:Class ;
    rdfs:subClassOf ex:Supplier .
ex:supplies a owl:ObjectProperty ;
    rdfs:domain ex:Supplier ;
    rdfs:range ex:Product .
"""

    response = client.post(
        f"/api/v1/ontologies/{ontology['id']}/files",
        files={"file": ("sample.ttl", ttl, "text/turtle")},
        headers=auth_headers,
    )

    assert response.status_code == 201
    assert response.json()["import"] == {"entities": 4, "relations": 2}

    entities = client.get(
        f"/api/v1/ontologies/{ontology['id']}/entities",
        headers=auth_headers,
    ).json()["data"]
    assert {entity["name_cn"] for entity in entities} >= {"Product", "Supplier", "PremiumSupplier", "supplies"}

    graph = client.get(
        f"/api/v1/ontologies/{ontology['id']}/graph",
        headers=auth_headers,
    ).json()["data"]
    assert graph["meta"]["entity_count"] == 4
    assert graph["meta"]["relation_count"] == 2
    assert {edge["data"]["label"] for edge in graph["edges"]} == {"subClassOf", "supplies"}
