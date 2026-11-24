import { Routes } from '@angular/router';

const loadLandingPage = () => import('./landing/landing-page').then((module) => module.LandingPage);

const loadEmployeePage = () =>
  import('./employee/employee-page').then((module) => module.EmployeePage);

const loadRepPage = () => import('./rep/rep-page').then((module) => module.RepPage);

export const routes: Routes = [
  { path: '', pathMatch: 'full', loadComponent: loadLandingPage },
  { path: 'employee', loadComponent: loadEmployeePage },
  { path: 'employee/:conversationId', loadComponent: loadEmployeePage },
  { path: 'rep', loadComponent: loadRepPage },
];
