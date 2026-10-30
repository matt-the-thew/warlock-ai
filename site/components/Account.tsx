import { SignInButton, UserButton, useAuth } from "@clerk/astro/react";

// The navbar's sign-in button, or the signed-in user's avatar menu. Renders
// nothing until Clerk has loaded so the button doesn't flash on reload.
export default function Account() {
  const { isLoaded, isSignedIn } = useAuth();
  if (!isLoaded) return null;
  if (isSignedIn) return <UserButton />;
  return (
    <SignInButton mode="modal">
      <button className="pixel-btn">SIGN IN</button>
    </SignInButton>
  );
}
