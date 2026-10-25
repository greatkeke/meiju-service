import { Routes } from '@angular/router';

export const routes: Routes = [
  { path: '', pathMatch: 'full', redirectTo: 'lan' },
  {
    path: 'login',
    loadComponent: () => import('./pages/login/login.component').then((m) => m.LoginComponent),
  },
  {
    path: 'lan',
    loadComponent: () => import('./pages/lan/lan.component').then((m) => m.LanComponent),
  },
  { path: '**', redirectTo: 'lan' },
];
