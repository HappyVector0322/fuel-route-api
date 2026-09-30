.PHONY: build up down logs test shell

build:  ## Build the Docker image
	docker compose build

up:     ## Start the API on http://localhost:8000
	docker compose up -d --build

down:   ## Stop the API
	docker compose down

logs:   ## Follow the API logs
	docker compose logs -f web

test:   ## Run the test suite inside the container
	docker compose run --rm --no-deps web python manage.py test routing

shell:  ## Open a Django shell inside the container
	docker compose run --rm web python manage.py shell
