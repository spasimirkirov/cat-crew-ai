---
name: automated-testing
description: QA by automated tests - write feature tests for every feature once, then re-run the suite on every check (Laravel, JavaScript, Python patterns).
---

# Automated testing

QA's job is proof that keeps working. A test written once and re-run on every check costs
seconds; walking every page by hand costs dozens of tool rounds each time and proves nothing
next week. Write tests for the features, run them, and use the browser only for what a test
cannot see (console errors, layout).

## The loop

1. Find the test runner and its style: `tests/` folder, `phpunit.xml`, `package.json` scripts,
   `pytest.ini` / `pyproject.toml`. Match the existing style (Pest or PHPUnit, Vitest or Jest).
2. Run the suite first. Note what already passes.
3. List the features: the brief, plus what the developer reported as new or changed.
4. One test file per feature area. Each test checks one behaviour, with a name that says it.
5. Run the suite. Red: decide whether the code or the test is wrong. Fix a wrong test and
   say so; report a code failure as FAIL with the test name and the assertion message.
6. Never delete or weaken a test to make it pass.

## Laravel (PHPUnit or Pest)

- Tests must never touch the real database: `phpunit.xml` should set
  `DB_CONNECTION=sqlite` and `DB_DATABASE=:memory:`. If those lines are commented out,
  enable them. Use `RefreshDatabase` in feature tests.
- Run: `php artisan test` (add `--filter=GalleryTest` for one file).
- Create a test: `php artisan make:test GalleryTest` (`--pest` for Pest).

```php
use App\Models\Gallery;
use App\Models\User;
use Illuminate\Foundation\Testing\RefreshDatabase;
use Illuminate\Http\UploadedFile;
use Illuminate\Support\Facades\Storage;
use Tests\TestCase;

class GalleryTest extends TestCase
{
    use RefreshDatabase;

    public function test_home_page_shows_the_service_sections(): void
    {
        $this->get('/')->assertOk()->assertSee('Цялостен ремонт')->assertSee('ВиК услуги');
    }

    public function test_guests_are_sent_to_the_login_from_the_admin(): void
    {
        $this->get('/admin')->assertRedirect('/login');
    }

    public function test_the_seeded_admin_can_log_in(): void
    {
        $this->seed();
        $this->post('/login', ['email' => 'admin@example.com', 'password' => 'password'])
            ->assertRedirect();
        $this->assertAuthenticated();
    }

    public function test_an_admin_creates_a_gallery_and_uploads_an_image(): void
    {
        Storage::fake('public');
        $admin = User::factory()->create();

        $this->actingAs($admin)->post('/admin/gallery', ['name' => 'Bathrooms'])->assertRedirect();
        $gallery = Gallery::firstOrFail();

        $file = UploadedFile::fake()->image('tile.jpg'); // needs GD; else ->create('tile.jpg', 100, 'image/jpeg')
        $this->actingAs($admin)
            ->post("/admin/gallery/{$gallery->id}/images", ['images' => [$file]])
            ->assertRedirect();

        $this->assertCount(1, $gallery->fresh()->images);
        Storage::disk('public')->assertExists($gallery->fresh()->images->first()->path);
    }
}
```

Read the real routes (`php artisan route:list --except-vendor`), the form field names and the
seeder before writing these: the URLs, fields and credentials above are examples.

Also cover: validation errors (`assertSessionHasErrors('name')`), the contact form (use
`Mail::fake()` and `Mail::assertSent()` when it sends mail), 404 for a missing gallery.

## JavaScript / TypeScript

- Vite projects: Vitest (`npx vitest run`), with `@testing-library/*` for components.
- Add a `"test": "vitest run"` script when there is none, so `npm test` works.
- End-to-end through a real browser only for the few flows that need it (Playwright).

## Python

- `pytest`, with FastAPI's `TestClient` or Django's test client for HTTP routes; temporary
  databases and `tmp_path` for files.

## The report

Start with PASS or FAIL. Then: tests run (passed / failed), the new tests you wrote (file
and what each covers), the harness smoke checks, and for each failure the test name, the URL
or file, and the assertion message.
