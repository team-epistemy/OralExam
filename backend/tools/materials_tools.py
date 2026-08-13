"""list_materials, get_material, list_material_versions, upload_material (T14)."""
from __future__ import annotations
from typing import List, Optional

from backend.models import (
    Caller, Role, MaterialSummary, MaterialVersion, PresignRequest,
)
from backend.db.repository import Repository
from backend.api.service import MaterialsApi, AuthorizationError


class MaterialsTools:
    """Read tools need course membership; upload needs professor role."""

    def __init__(self, repo: Repository, api: MaterialsApi,
                 is_member):
        self.repo = repo
        self.api = api
        self.is_member = is_member

    def list_materials(self, caller: Caller, course_id: str) -> List[MaterialSummary]:
        """Materials in a course with their current-version status."""
        self._require_member(caller, course_id)
        self.repo.set_tenant(caller.org_id)
        return [self._summary(m) for m in self.repo.list_materials(course_id)]

    def get_material(self, caller: Caller, material_id: str) -> MaterialSummary:
        """Material plus current-version detail, org-scoped."""
        self.repo.set_tenant(caller.org_id)
        material = self.repo.get_material(material_id)
        if not material:
            raise AuthorizationError("material not found")
        self._require_member(caller, material.course_id)
        return self._summary(material)

    def list_material_versions(self, caller: Caller,
                               material_id: str) -> List[MaterialVersion]:
        """All versions of a material in version order."""
        self.repo.set_tenant(caller.org_id)
        material = self.repo.get_material(material_id)
        if not material:
            raise AuthorizationError("material not found")
        self._require_member(caller, material.course_id)
        return self.repo.list_versions(material_id)

    def upload_material(self, caller: Caller, course_id: str,
                        req: PresignRequest):
        """Agent-friendly wrapper around presign (PUT + register stay client-side)."""
        return self.api.presign(caller, course_id, req)

    def _require_member(self, caller: Caller, course_id: str) -> None:
        """Any course membership suffices for read tools."""
        if not self.is_member(caller, course_id):
            raise AuthorizationError("course membership required")

    def _summary(self, material) -> MaterialSummary:
        """Build a summary, resolving the current version's status."""
        status = None
        if material.current_version_id:
            version = self.repo.get_version(material.current_version_id)
            status = version.status if version else None
        return MaterialSummary(material_id=material.material_id,
                               display_name=material.display_name,
                               current_version_id=material.current_version_id,
                               status=status)
