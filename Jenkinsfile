pipeline {
    agent { label 'docker-kubectl' }

    options {
        timestamps()
        disableConcurrentBuilds()
    }

    environment {
        IMAGE_REPOSITORY = 'ghcr.io/your-org/fakeaccount'
        K8S_NAMESPACE = 'fakeaccount'
    }

    stages {
        stage('Quality and tests') {
            steps {
                sh '''
                    python -m pip install -r requirements-dev.txt
                    ruff check fakeaccount fakeaccountapp scripts manage.py
                    DJANGO_DEBUG=True \
                    DJANGO_SECRET_KEY=ci-only-secret-key-value-with-more-than-fifty-characters \
                    DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1 \
                    BACKUP_ACTIVE_KEY_ID=ci \
                    BACKUP_ENCRYPTION_KEYS=ci:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA= \
                    python manage.py check
                    DJANGO_DEBUG=True \
                    DJANGO_SECRET_KEY=ci-only-secret-key-value-with-more-than-fifty-characters \
                    DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1 \
                    BACKUP_ACTIVE_KEY_ID=ci \
                    BACKUP_ENCRYPTION_KEYS=ci:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA= \
                    python manage.py test
                '''
            }
        }

        stage('Build and publish image') {
            when { branch 'main' }
            steps {
                withCredentials([usernamePassword(
                    credentialsId: 'container-registry',
                    usernameVariable: 'REGISTRY_USER',
                    passwordVariable: 'REGISTRY_PASSWORD'
                )]) {
                    sh '''
                        set +x
                        set -eu
                        IMAGE="$IMAGE_REPOSITORY:$GIT_COMMIT"
                        printf '%s' "$REGISTRY_PASSWORD" | docker login ghcr.io --username "$REGISTRY_USER" --password-stdin
                        trap 'docker logout ghcr.io >/dev/null 2>&1 || true' EXIT
                        docker build --pull --tag "$IMAGE" .
                        docker push "$IMAGE"
                        echo "$IMAGE" > .published-image
                    '''
                    stash name: 'published-image', includes: '.published-image'
                }
            }
        }

        stage('Deploy to Kubernetes') {
            when { branch 'main' }
            steps {
                unstash 'published-image'
                script {
                    def image = readFile('.published-image').trim()
                    sh "kubectl -n ${K8S_NAMESPACE} set image deployment/fakeaccount-web web=${image}"
                    sh "kubectl -n ${K8S_NAMESPACE} set image deployment/fakeaccount-backup-worker worker=${image}"
                    sh "kubectl -n ${K8S_NAMESPACE} rollout status deployment/fakeaccount-web --timeout=180s"
                    sh "kubectl -n ${K8S_NAMESPACE} rollout status deployment/fakeaccount-backup-worker --timeout=180s"
                }
            }
        }
    }

    post {
        always {
            sh 'rm -f .published-image'
        }
    }
}
