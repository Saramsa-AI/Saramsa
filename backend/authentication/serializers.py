from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.exceptions import TokenError
from django.contrib.auth.hashers import make_password
from .services import get_user_service


class AppUserSerializer(serializers.Serializer):
    """Serializer for PostgreSQL user data"""
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, min_length=6)
    confirmPassword = serializers.CharField(write_only=True, min_length=6)
    first_name = serializers.CharField(max_length=30, required=False, allow_blank=True)
    last_name = serializers.CharField(max_length=30, required=False, allow_blank=True)
    # Read-only so a client can NEVER set their own role via the public
    # registration/serializer input. A self-supplied role="admin" would
    # flow straight into profile.role, and _get_role_from_user(user) ==
    # "admin" short-circuits every permission check to True — i.e. global
    # privilege escalation. The server forces role="user" at the view layer;
    # legitimate role assignment happens through internal/admin paths only.
    role = serializers.CharField(max_length=50, read_only=True, default='user')

    def validate_email(self, value):
        """Reject already-registered emails behind the SAME generic message
        that RegisterView uses for bad/missing/mismatched invite tokens.

        Why so vague? A distinct "Email already exists" response would let
        an unauthenticated caller enumerate which emails are registered
        (info disclosure). The actual reason — duplicate email — is recorded
        server-side via the ValidationError; admins debugging a real "but I
        just tried to register and got this error" can find the cause in logs.
        """
        user_service = get_user_service()
        if user_service.get_user_by_email(value):
            raise serializers.ValidationError(
                "Invalid invitation. Please request a new invite from your workspace admin."
            )
        return value

    def validate(self, attrs):
        password = attrs.get('password')
        confirm_password = attrs.get('confirmPassword')
        if password and confirm_password and password != confirm_password:
            raise serializers.ValidationError({'confirmPassword': "Passwords don't match"})
        return attrs

    def hash_password(self, password):
        return make_password(password)


class AppTokenObtainPairSerializer(TokenObtainPairSerializer):
    """Issue simplejwt tokens for PostgreSQL users."""

    def validate(self, attrs):
        email = attrs.get('email')
        password = attrs.get('password')

        if not email or not password:
            raise serializers.ValidationError("Email and password are required")

        user_service = get_user_service()
        user_data = user_service.get_user_by_email(email)
        if not user_data:
            raise serializers.ValidationError("Invalid credentials")

        if not user_service._verify_password(password, user_data.get('password', '')):
            raise serializers.ValidationError("Invalid credentials")

        if not user_data.get('is_active', True):
            raise serializers.ValidationError("User account is disabled")

        from .org_context import build_user_with_org_context

        active_org_id = (user_data.get('profile') or {}).get('active_organization_id')

        refresh = RefreshToken()
        refresh['user_id'] = user_data.get('id')
        refresh['email'] = user_data.get('email')
        refresh['is_staff'] = user_data.get('is_staff', False)
        refresh['profile_role'] = user_data.get('profile', {}).get('role', 'user')
        if active_org_id:
            refresh['active_organization_id'] = active_org_id

        return {
            'access': str(refresh.access_token),
            'refresh': str(refresh),
            'user': build_user_with_org_context(user_data),
        }


class AppTokenRefreshSerializer(serializers.Serializer):
    """Refresh access token using simplejwt — handles rotation and blacklisting."""

    refresh = serializers.CharField()

    def validate(self, attrs):
        try:
            old_refresh = RefreshToken(attrs['refresh'])
        except TokenError:
            raise serializers.ValidationError("Invalid or expired refresh token")

        # Reject tokens revoked via logout/rotation (cache deny-list).
        from .token_denylist import is_refresh_token_denied
        if is_refresh_token_denied(old_refresh):
            raise serializers.ValidationError("Invalid or expired refresh token")

        user_id = old_refresh.get('user_id')
        if not user_id:
            raise serializers.ValidationError("Invalid refresh token")

        user_service = get_user_service()
        user_data = user_service.get_user_by_id(user_id)
        if not user_data:
            raise serializers.ValidationError("User not found")

        if not user_data.get('is_active', True):
            raise serializers.ValidationError("User account is disabled")

        # Rotation: revoke the old refresh token so it can't be reused (the
        # SimpleJWT DB blacklist is incompatible with our string user ids, so
        # we use the cache deny-list).
        from .token_denylist import deny_refresh_token
        deny_refresh_token(old_refresh)

        from .org_context import build_user_with_org_context

        active_org_id = (user_data.get('profile') or {}).get('active_organization_id')

        new_refresh = RefreshToken()
        new_refresh['user_id'] = user_data.get('id')
        new_refresh['email'] = user_data.get('email')
        new_refresh['is_staff'] = user_data.get('is_staff', False)
        new_refresh['profile_role'] = user_data.get('profile', {}).get('role', 'user')
        if active_org_id:
            new_refresh['active_organization_id'] = active_org_id

        return {
            'access': str(new_refresh.access_token),
            'user': build_user_with_org_context(user_data),
        }

class ForgotPasswordSerializer(serializers.Serializer):
    """Serializer for forgot password request"""
    email = serializers.EmailField()

class ResetPasswordSerializer(serializers.Serializer):
    """Serializer for password reset"""
    token = serializers.CharField()
    new_password = serializers.CharField(write_only=True, min_length=6)
    confirm_password = serializers.CharField(write_only=True, min_length=6)
    
    def validate(self, attrs):
        """Validate that passwords match"""
        new_password = attrs.get('new_password')
        confirm_password = attrs.get('confirm_password')
        
        if new_password and confirm_password and new_password != confirm_password:
            raise serializers.ValidationError({
                'confirm_password': "Passwords don't match"
            })
        
        return attrs
    
    def hash_password(self, password):
        """Hash password using bcrypt"""
        try:
            password_bytes = password.encode('utf-8')
            salt = bcrypt.gensalt()
            hashed = bcrypt.hashpw(password_bytes, salt)
            return hashed.decode('utf-8')
        except Exception as e:
            raise serializers.ValidationError(f"Password hashing failed: {str(e)}")


class AppUserRegisterSerializer(AppUserSerializer):
    """Serializer for invite-only registration. The view layer enforces
    a valid invite_token before this serializer's data is used."""
    pass

