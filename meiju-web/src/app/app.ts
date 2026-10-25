import { Component } from '@angular/core';
import { RouterOutlet } from '@angular/router';

/** Phone-shaped shell so the clone reads like the app it was traced from. */
@Component({
  imports: [RouterOutlet],
  selector: 'app-root',
  styleUrl: './app.scss',
  templateUrl: './app.html',
})
export class App {}
