"""T1: org A session sees zero org B rows (RLS emulation)."""
import pytest

from backend.models import Material
from backend.db.memory import InMemoryRepository, TenantViolation


def test_org_a_cannot_read_org_b_material():
    repo = InMemoryRepository()
    repo.set_tenant("org_b")
    mat_b = repo.create_material(Material(course_id="c1", org_id="org_b",
                                          created_by="u", display_name="B"))
    repo.set_tenant("org_a")
    with pytest.raises(TenantViolation):
        repo.get_material(mat_b.material_id)


def test_list_materials_filters_by_org():
    repo = InMemoryRepository()
    repo.set_tenant("org_b")
    repo.create_material(Material(course_id="c1", org_id="org_b",
                                  created_by="u", display_name="B"))
    repo.set_tenant("org_a")
    assert repo.list_materials("c1") == []
