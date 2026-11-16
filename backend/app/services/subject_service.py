"""Transactional management of academic subjects."""

from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from backend.app.models import Document, School, Subject, document_subjects
from backend.app.schemas import (
    SubjectCreate,
    SubjectDocumentSummary,
    SubjectResponse,
    SubjectUpdate,
)


def _database_unavailable() -> HTTPException:
    return HTTPException(503, "subject catalog is unavailable")


def _subject_uuid(subject_id: str) -> UUID:
    try:
        return UUID(subject_id)
    except ValueError as error:
        raise HTTPException(400, "subject_id must be a valid UUID") from error


def _response(subject: Subject) -> SubjectResponse:
    return SubjectResponse(
        id=subject.id,
        school_id=subject.school_id,
        code=subject.code,
        name=subject.name,
        description=subject.description,
        is_active=subject.is_active,
        created_at=subject.created_at,
    )


def create_subject(
    session_factory: sessionmaker[Session] | None,
    payload: SubjectCreate,
) -> SubjectResponse:
    if session_factory is None:
        raise _database_unavailable()
    try:
        with session_factory.begin() as session:
            school = session.get(School, payload.school_id)
            if school is None or not school.is_active:
                raise HTTPException(400, "unknown or inactive school_id")
            subject = Subject(
                school_id=payload.school_id,
                code=payload.code,
                name=payload.name,
                description=payload.description,
            )
            session.add(subject)
            session.flush()
            return _response(subject)
    except HTTPException:
        raise
    except IntegrityError as error:
        raise HTTPException(
            409, "subject code already exists in this school"
        ) from error
    except SQLAlchemyError as error:
        raise _database_unavailable() from error


def list_subjects(
    session_factory: sessionmaker[Session] | None,
    school_id: str | None,
) -> list[SubjectResponse]:
    if session_factory is None:
        raise _database_unavailable()
    try:
        with session_factory() as session:
            query = select(Subject).order_by(Subject.school_id, Subject.code)
            if school_id is not None:
                query = query.where(Subject.school_id == school_id)
            return [_response(subject) for subject in session.scalars(query)]
    except SQLAlchemyError as error:
        raise _database_unavailable() from error


def get_subject(
    session_factory: sessionmaker[Session] | None,
    subject_id: str,
) -> SubjectResponse:
    if session_factory is None:
        raise _database_unavailable()
    parsed_id = _subject_uuid(subject_id)
    try:
        with session_factory() as session:
            subject = session.get(Subject, parsed_id)
            if subject is None:
                raise HTTPException(404, "subject does not exist")
            return _response(subject)
    except HTTPException:
        raise
    except SQLAlchemyError as error:
        raise _database_unavailable() from error


def update_subject(
    session_factory: sessionmaker[Session] | None,
    subject_id: str,
    payload: SubjectUpdate,
) -> SubjectResponse:
    if session_factory is None:
        raise _database_unavailable()
    parsed_id = _subject_uuid(subject_id)
    try:
        with session_factory.begin() as session:
            subject = session.get(Subject, parsed_id)
            if subject is None:
                raise HTTPException(404, "subject does not exist")
            for field in payload.model_fields_set:
                value = getattr(payload, field)
                if field != "description" and value is None:
                    continue
                setattr(subject, field, value)
            session.flush()
            return _response(subject)
    except HTTPException:
        raise
    except IntegrityError as error:
        raise HTTPException(
            409, "subject code already exists in this school"
        ) from error
    except SQLAlchemyError as error:
        raise _database_unavailable() from error


def get_subject_documents(
    session_factory: sessionmaker[Session] | None,
    subject_id: str,
) -> list[SubjectDocumentSummary]:
    if session_factory is None:
        raise _database_unavailable()
    parsed_id = _subject_uuid(subject_id)
    try:
        with session_factory() as session:
            if session.get(Subject, parsed_id) is None:
                raise HTTPException(404, "subject does not exist")
            documents = session.scalars(
                select(Document)
                .join(
                    document_subjects,
                    document_subjects.c.document_id == Document.id,
                )
                .where(
                    document_subjects.c.subject_id == parsed_id,
                    Document.deleted_at.is_(None),
                )
                .order_by(Document.created_at.desc())
            ).all()
            return [
                SubjectDocumentSummary(
                    document_id=document.id,
                    title=document.title,
                    description=document.description,
                    school_id=document.school_id,
                    document_type_id=document.document_type_id,
                    created_at=document.created_at,
                    current_version_id=document.current_version_id,
                )
                for document in documents
            ]
    except HTTPException:
        raise
    except SQLAlchemyError as error:
        raise _database_unavailable() from error
