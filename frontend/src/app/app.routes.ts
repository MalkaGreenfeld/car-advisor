import { Routes } from '@angular/router';

const loadEmployeePage = () =>
  import('./employee/employee-page').then((module) => module.EmployeePage);

const loadRepPage = () => import('./rep/rep-page').then((module) => module.RepPage);

export const routes: Routes = [
  { path: '', pathMatch: 'full', redirectTo: 'employee' },
  { path: 'employee', loadComponent: loadEmployeePage },
  { path: 'employee/:conversationId', loadComponent: loadEmployeePage },
  { path: 'rep', loadComponent: loadRepPage },
];
