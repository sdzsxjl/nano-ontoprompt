"""
One-shot repair script: find isolated entities in each domain ontology,
call infer_relations to generate missing edges, then save with fuzzy matching.
Run from the backend directory: python repair_isolated_nodes.py
"""
import sys, uuid
sys.path.insert(0, ".")

from app.database import SessionLocal
from app.models.ontology import OntologyProject
from app.models.entity import Entity
from app.models.relation import Relation
from app.models.model_config import ModelConfig
from app.models.extraction_task import ExtractionTask
from app.models.file import UploadedFile
from app.services.llm_service import infer_relations
from app.services.encryption_service import decrypt


MODEL_ID   = "dd837230-70ad-4784-bcbe-e30ae8679866"
MODEL_NAME = "deepseek-chat"

TARGET_NAMES = ["财务域", "医疗域", "营销域", "HR域"
                "供应链知识图谱-LLM提取"]
# TARGET_NAMES = ["供应链知识图谱-LLM提取"]

def fuzzy_resolve(name: str, name_to_id: dict):
    if not name:
        return None
    if name in name_to_id:
        return name_to_id[name]
    candidates = [(kn, eid) for kn, eid in name_to_id.items()
                  if kn and (name in kn or kn in name)]
    if not candidates:
        return None
    candidates.sort(key=lambda x: len(set(x[0]) & set(name)), reverse=True)
    return candidates[0][1]


def repair_ontology(db, o, config_dict):
    entities = db.query(Entity).filter(Entity.ontology_id == o.id).all()
    relations = db.query(Relation).filter(Relation.ontology_id == o.id).all()

    entity_id_to_name = {e.id: e.name_cn for e in entities}
    name_to_id = {e.name_cn: e.id for e in entities}
    for e in entities:
        if e.name_en:
            name_to_id[e.name_en] = e.id

    in_relation = set()
    for r in relations:
        in_relation.add(r.source_entity)
        in_relation.add(r.target_entity)

    isolated_ids = {e.id for e in entities} - in_relation
    if not isolated_ids:
        print(f"  [跳过] 无孤立节点")
        return 0

    print(f"  孤立实体 {len(isolated_ids)} 个: {[entity_id_to_name[i] for i in isolated_ids]}")

    # Build entity dicts for infer_relations (same shape as LLM output)
    all_entity_dicts = [{"name_cn": e.name_cn, "name_en": e.name_en, "type": e.type} for e in entities]
    existing_rel_dicts = [
        {"source": entity_id_to_name.get(r.source_entity, ""),
         "target": entity_id_to_name.get(r.target_entity, ""),
         "type": r.type}
        for r in relations
        if r.source_entity in entity_id_to_name and r.target_entity in entity_id_to_name
    ]

    # Get source text from uploaded files
    files = db.query(UploadedFile).filter(UploadedFile.ontology_id == o.id).all()
    text = "\n\n".join(f.converted_md or "" for f in files if f.converted_md)[:8000]

    try:
        extra_rels = infer_relations(all_entity_dicts, existing_rel_dicts, text, config_dict, MODEL_NAME)
    except Exception as ex:
        print(f"  [ERROR] infer_relations failed: {ex}")
        return 0

    existing_rel_set = {(r.source_entity, r.target_entity, r.type) for r in relations}
    added = 0
    for rel in (extra_rels or []):
        src_name = rel.get("source", "")
        tgt_name = rel.get("target", "")
        src_id = fuzzy_resolve(src_name, name_to_id)
        tgt_id = fuzzy_resolve(tgt_name, name_to_id)
        rel_type = rel.get("type", "关联")
        if src_id and tgt_id and src_id != tgt_id and (src_id, tgt_id, rel_type) not in existing_rel_set:
            db.add(Relation(
                id=str(uuid.uuid4()), ontology_id=o.id,
                source_entity=src_id, target_entity=tgt_id,
                type=rel_type, confidence=rel.get("confidence", 0.70),
            ))
            existing_rel_set.add((src_id, tgt_id, rel_type))
            added += 1

    db.commit()
    return added


def main():
    db = SessionLocal()
    model = db.query(ModelConfig).filter(ModelConfig.id == MODEL_ID).first()
    config_dict = {
        "provider": model.provider,
        "api_key":  decrypt(model.api_key_encrypted or ""),
        "api_base": model.api_base,
    }

    for name_prefix in TARGET_NAMES:
        matches = db.query(OntologyProject).filter(
            OntologyProject.name.like(f"{name_prefix}%")
        ).order_by(OntologyProject.created_at.desc()).all()

        # For 供应链 only take the latest one with entities
        if name_prefix == "供应链知识图谱-LLM提取":
            matches = [m for m in matches if db.query(Entity).filter(Entity.ontology_id == m.id).count() > 10][:1]

        for o in matches:
            ent_count = db.query(Entity).filter(Entity.ontology_id == o.id).count()
            if ent_count == 0:
                continue
            print(f"\n[{o.domain}] {o.name}  (id={o.id})")
            added = repair_ontology(db, o, config_dict)
            print(f"  新增关系: {added}")

    db.close()
    print("\n完成")


if __name__ == "__main__":
    main()
