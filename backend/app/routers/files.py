import os
import uuid
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session
from app.deps import get_db, get_current_user
from app.models.file import UploadedFile
from app.models.ontology import OntologyProject
from app.schemas.file import FileOut
from app.services.document_service import convert_to_markdown
from app.services.owl_import_service import import_owl_graph, is_owl_like_file
from app.config import settings

router = APIRouter()

ALLOWED_TYPES = {
    "application/pdf", "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "text/csv", "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "image/png", "image/jpeg", "text/markdown", "text/plain",
    "application/msword", "application/vnd.ms-excel",
}

@router.get("")
def list_files(ontology_id: str, db: Session = Depends(get_db), _=Depends(get_current_user)):
    files = db.query(UploadedFile).filter(UploadedFile.ontology_id == ontology_id).all()
    return {"data": [FileOut.model_validate(f).model_dump() for f in files]}

@router.post("", status_code=201)
async def upload_file(
    ontology_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _=Depends(get_current_user)
):
    project = db.query(OntologyProject).filter(OntologyProject.id == ontology_id).first()
    if not project:
        raise HTTPException(404, "Ontology not found")

    ext_name = (file.filename or "").rsplit(".", 1)[-1].lower()
    allowed = {e.strip() for e in settings.allowed_upload_extensions.split(",") if e.strip()}
    if ext_name not in allowed:
        raise HTTPException(400, f"不支持的文件类型: .{ext_name} (允许: {settings.allowed_upload_extensions})")

    upload_dir = os.path.join(settings.uploads_dir, ontology_id)
    os.makedirs(upload_dir, exist_ok=True)

    file_id = str(uuid.uuid4())
    ext = os.path.splitext(file.filename)[1]
    save_path = os.path.join(upload_dir, f"{file_id}{ext}")

    content = await file.read()
    if len(content) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(413, f"文件超过大小限制 {settings.max_upload_mb}MB")
    with open(save_path, "wb") as f:
        f.write(content)

    mime = file.content_type or "application/octet-stream"
    converted = convert_to_markdown(save_path, mime)

    db_file = UploadedFile(
        id=file_id,
        ontology_id=ontology_id,
        filename=file.filename,
        file_path=save_path,
        file_size=len(content),
        mime_type=mime,
        converted_md=converted,
    )
    import_result = None
    owl_file = is_owl_like_file(save_path)
    owl_attempted = False
    try:
        db.add(db_file)
        if owl_file:
            owl_attempted = True
            import_result = import_owl_graph(db, ontology_id, save_path)
        db.commit()
        db.refresh(db_file)
    except Exception as e:
        db.rollback()
        if os.path.exists(save_path):
            os.remove(save_path)
        if owl_attempted:
            raise HTTPException(400, f"OWL import failed: {e}")
        raise

    if import_result:
        return {"data": FileOut.model_validate(db_file).model_dump(), "import": import_result}
    return {"data": FileOut.model_validate(db_file).model_dump()}

@router.delete("/{file_id}", status_code=204)
def delete_file(ontology_id: str, file_id: str, db: Session = Depends(get_db), _=Depends(get_current_user)):
    f = db.query(UploadedFile).filter(UploadedFile.id == file_id, UploadedFile.ontology_id == ontology_id).first()
    if not f:
        raise HTTPException(404, "File not found")
    if os.path.exists(f.file_path):
        os.remove(f.file_path)
    db.delete(f); db.commit()
