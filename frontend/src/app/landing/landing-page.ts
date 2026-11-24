import { Component } from '@angular/core';
import { RouterLink } from '@angular/router';

import { landingCopy } from './landing.copy';

@Component({
  selector: 'app-landing-page',
  imports: [RouterLink],
  templateUrl: './landing-page.html',
  styleUrl: './landing-page.scss',
})
export class LandingPage {
  protected readonly copy = landingCopy;
}
