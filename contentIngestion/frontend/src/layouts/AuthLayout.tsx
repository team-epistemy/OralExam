import { Outlet } from 'react-router-dom';
import { GraduationCap } from 'lucide-react';

export default function AuthLayout() {
  return (
    <div className="min-h-screen bg-parchment flex items-center justify-center p-4">
      <div className="w-full max-w-md">
        <div className="text-center mb-8">
          <div className="inline-flex items-center justify-center w-12 h-12 bg-navy rounded-xl mb-4">
            <GraduationCap className="w-7 h-7 text-gold" />
          </div>
          <h1 className="text-3xl font-heading text-navy">
            <span className="text-gold">E</span>pistemy
          </h1>
          <p className="text-sm text-muted mt-1 italic">AI-Powered Oral Examinations</p>
        </div>
        <div className="bg-white rounded-xl shadow-sm border border-border p-6">
          <Outlet />
        </div>
      </div>
    </div>
  );
}
