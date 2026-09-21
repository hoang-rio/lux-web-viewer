FROM node:24-alpine AS fe_builder

WORKDIR /fe_builder/fe_src
COPY web_viewer/fe_src/package.json web_viewer/fe_src/yarn.lock* web_viewer/fe_src/.yarnrc.yml ./
COPY web_viewer/fe_src/.yarn ./.yarn
RUN yarn install

COPY web_viewer/fe_src .
# Build the frontend
RUN yarn build

FROM python:3.13-alpine

COPY . /app

WORKDIR /app
# Copy the frontend build from the builder stage
COPY --from=fe_builder /fe_builder/build /app/web_viewer/build

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt
# Expose the ports the app runs on
EXPOSE 88
EXPOSE 8443

VOLUME [ "/app/.env", "/app/db/web.db" ]

CMD [ "python", "app.py" ]
