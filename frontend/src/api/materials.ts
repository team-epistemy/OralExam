import { get, post, putFile } from './client';

export interface PresignResponse {
  material_id: string;
  material_version_id: string;
  version_no: number;
  s3_key: string;
  upload_url: string;
}

export interface MaterialVersion {
  material_version_id: string;
  material_id: string;
  version_no: number;
  status: string;
  file_name: string;
  source_type: string;
  created_at: string;
}

export interface Material {
  material_id: string;
  display_name: string;
  current_version_id: string | null;
}

export async function presignUpload(
  orgName: string,
  courseName: string,
  fileName: string,
  mimeType: string,
  bytes: number,
  displayName?: string,
): Promise<PresignResponse> {
  return post<PresignResponse>('/materials:presign', {
    org_name: orgName,
    course_name: courseName,
    file_name: fileName,
    mime_type: mimeType,
    bytes: bytes,
    display_name: displayName || undefined,
  });
}

export async function uploadToS3(url: string, file: File): Promise<void> {
  await putFile(url, file);
}

export async function registerMaterial(
  _orgName: string,
  versionId: string,
): Promise<unknown> {
  return post(`/versions/${versionId}/register`);
}

export async function listMaterials(orgNameOrCourseId: string, courseName?: string): Promise<Material[]> {
  // If courseName is provided, use the name-based endpoint
  if (courseName) {
    try {
      return await get<Material[]>(
        `/orgs/${encodeURIComponent(orgNameOrCourseId)}/courses/${encodeURIComponent(courseName)}/materials`
      );
    } catch {
      return [];
    }
  }
  // Otherwise, treat as course_id and fetch via dashboard data
  try {
    const data: any = await get('/api/professor/dashboard');
    const course = (data.courses || []).find((c: { course_id: string }) => c.course_id === orgNameOrCourseId);
    if (!course) return [];
    const uploads = (data.recent_uploads || []).filter((u: { course_name: string }) => u.course_name === course.course_name);
    return uploads.map((u: { material_version_id: string; file_name: string; status: string; created_at: string }) => ({
      material_id: u.material_version_id,
      display_name: u.file_name,
      status: u.status,
      created_at: u.created_at,
    }));
  } catch {
    return [];
  }
}

export interface MaterialView {
  url: string;
  file_name: string;
  source_type: string;
  version_id: string;
  version_no: number;
  status: string;
}

export async function getMaterialView(materialId: string): Promise<MaterialView> {
  return get<MaterialView>(`/api/materials/${encodeURIComponent(materialId)}/view`);
}

export async function listVersions(_orgName: string, materialId: string): Promise<MaterialVersion[]> {
  try {
    return await get<MaterialVersion[]>(`/materials/${materialId}/versions`);
  } catch {
    return [];
  }
}

export async function uploadMaterial(
  orgName: string,
  courseName: string,
  file: File,
  onProgress?: (pct: number) => void,
  topic?: string,
): Promise<PresignResponse> {
  onProgress?.(10);
  const presign = await presignUpload(orgName, courseName, file.name, file.type, file.size, topic);
  onProgress?.(30);
  await uploadToS3(presign.upload_url, file);
  onProgress?.(70);
  await registerMaterial(orgName, presign.material_version_id);
  onProgress?.(100);
  return presign;
}
